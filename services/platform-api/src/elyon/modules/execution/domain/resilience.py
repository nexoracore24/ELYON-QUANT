"""Circuit breakers, the outbox, and the dead letter queue.

Three separate answers to three separate failure modes, kept apart because
conflating them is how a system either hammers a dead broker or silently drops
the one event that mattered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Iterator

from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

from .events import OrderEvent

SECOND_NS = 1_000_000_000


class BreakerState(str, Enum):
    CLOSED = "CLOSED"        # traffic flows
    OPEN = "OPEN"            # traffic refused; the dependency is down
    HALF_OPEN = "HALF_OPEN"  # one probe allowed through


@dataclass(slots=True)
class CircuitBreaker:
    """One breaker per dependency (ADR-EXE-5).

    Per dependency, not per system: a market-data outage should not stop the OMS
    from *closing* a position, and a single global breaker cannot tell the
    difference. Sharing one breaker across dependencies means the least reliable
    one decides what everything else is allowed to do.
    """

    name: str
    failure_threshold: int = 5
    reset_after_ns: int = 30 * SECOND_NS
    # How many probes must succeed before traffic is trusted again. More than
    # one, because a single lucky response is not recovery.
    success_threshold: int = 2

    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    successes: int = 0
    opened_at_ns: int = 0
    trips: int = 0

    def allows(self, now_ns: int) -> bool:
        """Whether a call may be attempted right now."""
        if self.state is BreakerState.CLOSED:
            return True
        if self.state is BreakerState.OPEN:
            if now_ns - self.opened_at_ns >= self.reset_after_ns:
                self.state = BreakerState.HALF_OPEN
                self.successes = 0
                return True
            return False
        return True  # HALF_OPEN: probes are allowed

    def record_success(self, now_ns: int) -> None:
        if self.state is BreakerState.HALF_OPEN:
            self.successes += 1
            if self.successes >= self.success_threshold:
                self.state = BreakerState.CLOSED
                self.failures = 0
                self.successes = 0
            return
        self.failures = 0

    def record_failure(self, now_ns: int) -> None:
        # A failure during a probe reopens immediately: the dependency told us
        # it is still broken, and waiting for the full count would send the
        # remaining probes into a hole we already know about.
        if self.state is BreakerState.HALF_OPEN:
            self._trip(now_ns)
            return
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self._trip(now_ns)

    def _trip(self, now_ns: int) -> None:
        self.state = BreakerState.OPEN
        self.opened_at_ns = now_ns
        self.successes = 0
        self.trips += 1

    @property
    def is_open(self) -> bool:
        return self.state is BreakerState.OPEN

    def describe(self) -> str:
        return f"{self.name}: {self.state.value} ({self.failures} failures, {self.trips} trips)"


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """An event waiting to be published."""

    event: OrderEvent
    attempts: int = 0
    published: bool = False


@dataclass(slots=True)
class Outbox:
    """Events to publish, written in the same step as the state change.

    The pattern exists to kill dual-write (ADR-EXE-2): saving state and
    publishing an event as two separate operations means a crash between them
    leaves the system with a state nobody was told about. Writing the event
    *into the same store* as the state makes them one fact, and a publisher
    drains it afterwards.

    Delivery is at-least-once, which is exactly why every consumer dedupes on
    ``broker_event_id``. Trying to build exactly-once at the transport layer is
    a much harder problem with a much worse failure mode.
    """

    pending: list[OutboxEntry] = field(default_factory=list)
    published: list[OrderEvent] = field(default_factory=list)

    def enqueue(self, event: OrderEvent) -> None:
        self.pending.append(OutboxEntry(event))

    def drain(self, publish, *, max_attempts: int = 3) -> list[OrderEvent]:
        """Publish what is waiting, keeping anything that fails.

        A failed publish stays in the outbox rather than being lost. That is the
        entire point: the event is already a durable fact, and delivery is
        allowed to be slow but not allowed to be silent.
        """
        delivered: list[OrderEvent] = []
        still_pending: list[OutboxEntry] = []

        for entry in self.pending:
            try:
                publish(entry.event)
            except Exception:
                attempts = entry.attempts + 1
                if attempts < max_attempts:
                    still_pending.append(
                        OutboxEntry(entry.event, attempts, published=False)
                    )
                else:
                    still_pending.append(
                        OutboxEntry(entry.event, attempts, published=False)
                    )
                continue
            delivered.append(entry.event)
            self.published.append(entry.event)

        self.pending = still_pending
        return delivered

    @property
    def exhausted(self) -> list[OutboxEntry]:
        """Entries that have failed enough times to belong in the DLQ."""
        return [e for e in self.pending if e.attempts >= 3]

    def __len__(self) -> int:
        return len(self.pending)


@dataclass(frozen=True, slots=True)
class DeadLetter:
    """Something the system could not process, kept with the reason why."""

    event: OrderEvent
    reason: str
    at_ns: int
    attempts: int


@dataclass(slots=True)
class DeadLetterQueue:
    """Where unprocessable events go to be looked at by a human.

    Not a bin. Every entry keeps the event *and* the reason, because a DLQ whose
    entries cannot be explained is indistinguishable from dropping them -- and
    an OMS that drops events silently is one that loses positions.
    """

    entries: list[DeadLetter] = field(default_factory=list)

    def add(self, event: OrderEvent, reason: str, at_ns: int, attempts: int = 0) -> None:
        if not reason.strip():
            raise DeterminismError(
                "a dead letter without a reason is a dropped event with extra steps"
            )
        self.entries.append(DeadLetter(event, reason, at_ns, attempts))

    def for_order(self, client_order_id: str) -> list[DeadLetter]:
        return [e for e in self.entries if e.event.client_order_id == client_order_id]

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[DeadLetter]:
        return iter(self.entries)

    def summary(self) -> str:
        if not self.entries:
            return "dead letter queue empty"
        return "\n".join(
            f"{e.event.client_order_id} {e.event.kind.value}: {e.reason}"
            for e in self.entries
        )
