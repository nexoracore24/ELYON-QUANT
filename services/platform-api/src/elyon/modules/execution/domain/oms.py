"""The Order Management System.

Every execution goes through here. There is no shortcut to the broker, which is
the point: an order placed outside the OMS has no event log, no idempotency key
and no reconciliation, so nobody can say afterwards what happened or why.

The hardest problem this file solves is one sentence long: **a send that times
out has an unknown outcome.** The order may be resting at the broker, may have
filled, or may never have arrived. Retrying blindly risks a double position --
the most expensive bug an OMS can have, because it doubles risk silently and the
account looks fine until it does not.

The answer (ADR-EXE-4) is to never guess. On a timeout the OMS *asks*:

    query(client_order_id)
      exists  → adopt it; the order was already placed
      absent  → resend, with the same client order id so the broker can dedupe

And when the answer is still unknown after that, the system does the one thing
that is always safe: it stops sending and protects what is open.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Iterator, Mapping

from elyon.shared_kernel.edcs.canonical import stable_id
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec

from .events import EventKind, OrderEvent, Side
from .order import (
    IllegalTransition,
    Order,
    OrderRequest,
    OrderState,
)
from .ports import (
    BrokerAdapter,
    BrokerError,
    BrokerErrorKind,
    BrokerOrderState,
    Clock,
)
from .resilience import CircuitBreaker, DeadLetterQueue, Outbox
from .store import EventStore, InMemoryEventStore

SECOND_NS = 1_000_000_000


def client_order_id(correlation_id: str, leg: str = "entry", attempt_group: int = 0) -> str:
    """A stable, deterministic identity for an order.

    Deterministic on purpose: a retry must produce the *same* id so the broker
    can recognise the duplicate. An id derived from a timestamp or a random
    source would make every retry look like a brand-new order, which is exactly
    how duplicate positions happen.
    """
    return str(stable_id(
        namespace="elyon.execution.order",
        key={
            "correlationId": correlation_id,
            "leg": leg,
            "attemptGroup": attempt_group,
        },
    ))


def idempotency_key(coid: str, command: str, attempt_group: int = 0) -> str:
    """The key for one command against one order."""
    return str(stable_id(
        namespace="elyon.execution.command",
        key={"clientOrderId": coid, "command": command, "attemptGroup": attempt_group},
    ))


@dataclass(frozen=True, slots=True)
class OmsConfig:
    max_send_retries: int = 3
    backoff_base_ns: int = 1 * SECOND_NS
    backoff_cap_ns: int = 30 * SECOND_NS
    ack_timeout_ns: int = 5 * SECOND_NS

    def backoff(self, attempt: int) -> int:
        """Exponential with a ceiling, computed from the injected clock.

        No jitter here: jitter helps a fleet avoid synchronised retries, but it
        also makes a backtest irreproducible, and reproducibility is the more
        valuable property for a single account.
        """
        return min(self.backoff_base_ns * (2 ** max(0, attempt)), self.backoff_cap_ns)


@dataclass(frozen=True, slots=True)
class SendOutcome:
    """What happened when the OMS tried to place an order."""

    order: Order
    sent: bool
    adopted: bool = False
    reason: str = ""

    def __str__(self) -> str:
        if self.adopted:
            return f"adopted existing order at broker: {self.reason}"
        if self.sent:
            return f"sent: {self.order.client_order_id}"
        return f"not sent: {self.reason}"


class SafeHalt(Exception):
    """The OMS has stopped sending. Open positions are protected, not closed."""


@dataclass(slots=True)
class Oms:
    """The order management system.

    Holds the event log, the projections, the outbox and the breakers. Nothing
    here talks to a broker directly; the adapter does, and only through the
    guarded paths below.
    """

    adapter: BrokerAdapter
    clock: Clock
    config: OmsConfig = field(default_factory=OmsConfig)
    breaker: CircuitBreaker = field(
        default_factory=lambda: CircuitBreaker("broker")
    )
    outbox: Outbox = field(default_factory=Outbox)
    dlq: DeadLetterQueue = field(default_factory=DeadLetterQueue)
    # Where the log survives a restart. The in-memory default is right for a
    # backtest and wrong for anything holding a position: without a durable
    # store, "the OMS recovers what it was doing" is true only until the
    # process ends.
    store: EventStore = field(default_factory=InMemoryEventStore)

    _log: dict[str, list[OrderEvent]] = field(default_factory=dict)
    _requests: dict[str, OrderRequest] = field(default_factory=dict)
    _sequence: int = 0
    _halted: bool = False
    _halt_reason: str = ""

    # -- the log ----------------------------------------------------------

    def _record(self, event: OrderEvent) -> OrderEvent:
        """Persist an event, then hand it to the outbox.

        Order matters: the event becomes a durable fact before anyone is told
        about it. Publishing first and persisting after is the dual-write bug --
        a crash in between announces something that never happened.
        """
        self._sequence += 1
        stamped = OrderEvent(
            kind=event.kind,
            client_order_id=event.client_order_id,
            at_ns=event.at_ns,
            sequence=self._sequence,
            broker_event_id=event.broker_event_id,
            quantity=event.quantity,
            price=event.price,
            reason=event.reason,
            payload=event.payload,
        )
        self._log.setdefault(event.client_order_id, []).append(stamped)
        # Durable before anyone is told. The outbox is a publication queue; the
        # store is the record, and the record has to exist first.
        self.store.append_event(stamped)
        self.outbox.enqueue(stamped)
        return stamped

    def log_of(self, coid: str) -> tuple[OrderEvent, ...]:
        return tuple(self._log.get(coid, ()))

    def order(self, coid: str) -> Order:
        """The current projection, rebuilt from the log every time.

        Rebuilding rather than caching is deliberate at this size: it makes the
        projection provably a function of the log, so a divergence between the
        two is impossible rather than merely unlikely.
        """
        request = self._requests.get(coid)
        if request is None:
            raise DeterminismError(f"unknown order {coid}")
        return Order.replay(request, self._log[coid])

    @property
    def orders(self) -> tuple[Order, ...]:
        return tuple(self.order(coid) for coid in sorted(self._log))

    def open_orders(self) -> tuple[Order, ...]:
        return tuple(o for o in self.orders if not o.state.is_terminal)

    def exposed_orders(self) -> tuple[Order, ...]:
        """Orders with real money at risk right now."""
        return tuple(o for o in self.orders if o.state.has_exposure)

    # -- lifecycle --------------------------------------------------------

    def create(self, request: OrderRequest) -> Order:
        """Register an order. Nothing has been sent."""
        if request.client_order_id in self._requests:
            # Idempotent: the same order id is the same order, not a second one.
            return self.order(request.client_order_id)

        self._requests[request.client_order_id] = request
        self._log[request.client_order_id] = []
        self.store.append_order(request)
        self._record(OrderEvent(
            kind=EventKind.CREATED,
            client_order_id=request.client_order_id,
            at_ns=self.clock.now_ns(),
            payload={"symbol": request.symbol, "side": request.side.value},
        ))
        return self.order(request.client_order_id)

    def _emit(self, coid: str, kind: EventKind, **kwargs) -> Order:
        event = OrderEvent(
            kind=kind,
            client_order_id=coid,
            at_ns=self.clock.now_ns(),
            **kwargs,
        )
        # Validate against the current projection before persisting: an event
        # the machine would refuse must never reach the log.
        self.order(coid).apply(event)
        self._record(event)
        return self.order(coid)

    def validate(self, coid: str, *, reason: str = "structural checks passed") -> Order:
        return self._emit(coid, EventKind.VALIDATED, reason=reason)

    def approve_risk(self, coid: str, *, reason: str = "risk approved") -> Order:
        """Risk approval is a blocking contract with ENG-005.

        There is deliberately no path from VALIDATED to QUEUED that skips it.
        """
        return self._emit(coid, EventKind.RISK_APPROVED, reason=reason)

    def queue(self, coid: str) -> Order:
        if self._halted:
            return self._emit(
                coid, EventKind.SAFE_HALTED,
                reason=f"OMS halted: {self._halt_reason}",
            )
        return self._emit(coid, EventKind.QUEUED)

    def reject(self, coid: str, reason: str) -> Order:
        return self._emit(coid, EventKind.REJECTED, reason=reason)

    def cancel(self, coid: str, reason: str = "cancelled") -> Order:
        return self._emit(coid, EventKind.CANCELLED, reason=reason)

    # -- sending ----------------------------------------------------------

    def send(self, coid: str) -> SendOutcome:
        """Place a queued order, exactly once.

        The guard is the state machine itself: QUEUED is the only state that
        transitions to SENT, so a second call cannot produce a second order --
        it produces an IllegalTransition, which is the correct answer.
        """
        order = self.order(coid)

        if self._halted:
            return SendOutcome(order, sent=False, reason=f"halted: {self._halt_reason}")

        if order.state is not OrderState.QUEUED:
            return SendOutcome(
                order, sent=False,
                reason=f"not queued (state {order.state.value}); "
                       f"refusing to send twice",
            )

        now = self.clock.now_ns()
        if not self.breaker.allows(now):
            return SendOutcome(
                order, sent=False,
                reason=f"circuit breaker open for {self.breaker.name}",
            )

        # The SENT event is written *before* the network call. If the process
        # dies during the call, recovery finds the record and knows to ask the
        # broker rather than assume nothing happened.
        order = self._emit(coid, EventKind.SENT)
        key = idempotency_key(coid, "place")

        try:
            ack = self.adapter.place(order.request, key)
        except BrokerError as exc:
            self.breaker.record_failure(now)
            return self._handle_send_failure(coid, exc)

        self.breaker.record_success(now)
        order = self._emit(
            coid, EventKind.ACKNOWLEDGED,
            payload={"brokerOrderId": ack.broker_order_id},
        )
        return SendOutcome(order, sent=True)

    def _handle_send_failure(self, coid: str, error: BrokerError) -> SendOutcome:
        """Decide what a failed send means. This is the whole ballgame.

        A rejection is a fact and can be recorded. A timeout is not: the order
        may be alive at the broker. Resending on a maybe is how one intended
        position becomes two.
        """
        order = self.order(coid)

        if not error.outcome_is_unknown:
            # The broker told us it refused. That is information, not doubt.
            return SendOutcome(
                self.reject(coid, f"broker rejected: {error}"),
                sent=False, reason=str(error),
            )

        # Unknown outcome. Ask before doing anything.
        return self.reconcile(coid, trigger=f"send failed: {error}")

    def reconcile(self, coid: str, *, trigger: str = "reconciliation") -> SendOutcome:
        """Ask the broker what is actually true, and adopt the answer.

        The broker is the authority (ADR-EXE-8). Where the OMS and the broker
        disagree, the OMS is wrong by definition -- it is the one that was not
        holding the position.
        """
        order = self._emit(coid, EventKind.RECOVERY_STARTED, reason=trigger)
        now = self.clock.now_ns()

        try:
            remote = self.adapter.query(coid)
        except BrokerError as exc:
            self.breaker.record_failure(now)
            # Cannot see the broker, cannot know the truth. The only safe move
            # is to stop sending and protect what is open.
            self.halt(f"cannot reach broker to reconcile {coid}: {exc}")
            return SendOutcome(
                self.order(coid), sent=False,
                reason=f"reconciliation failed, OMS halted: {exc}",
            )

        self.breaker.record_success(now)

        if remote.exists:
            return SendOutcome(
                self._adopt(coid, remote), sent=True, adopted=True,
                reason=f"order already at broker ({remote.broker_order_id})",
            )

        # Genuinely absent. Now, and only now, a resend is safe -- and it goes
        # out under the same client order id so the broker can still dedupe.
        return self._resend(coid, trigger)

    def _adopt(self, coid: str, remote: BrokerOrderState) -> Order:
        """Take on the broker's version of an order the OMS was unsure about."""
        target = remote.state or OrderState.ACKNOWLEDGED
        order = self._emit(
            coid, EventKind.RECONCILED,
            reason=f"adopted broker state {target.value}",
            payload={
                "state": target.value,
                "brokerOrderId": remote.broker_order_id or "",
            },
        )
        # Fold in any fills the OMS had not seen. Dedup by broker_event_id means
        # replaying ones it already had is harmless.
        for fill in remote.fills:
            kind = (
                EventKind.FILLED
                if order.filled_quantity + fill.quantity >= order.request.quantity
                else EventKind.PARTIALLY_FILLED
            )
            try:
                order = self._emit(
                    coid, kind,
                    broker_event_id=fill.broker_event_id,
                    quantity=fill.quantity,
                    price=fill.price,
                    reason="reconciled fill",
                )
            except (IllegalTransition, DeterminismError) as exc:
                self.dlq.add(
                    OrderEvent(
                        kind=kind, client_order_id=coid,
                        at_ns=self.clock.now_ns(),
                        broker_event_id=fill.broker_event_id,
                        quantity=fill.quantity, price=fill.price,
                    ),
                    reason=f"reconciled fill could not be applied: {exc}",
                    at_ns=self.clock.now_ns(),
                )
        return self.order(coid)

    def _resend(self, coid: str, trigger: str) -> SendOutcome:
        order = self.order(coid)
        if order.send_attempts > self.config.max_send_retries:
            failed = self._emit(
                coid, EventKind.FAILED,
                reason=f"{order.send_attempts} attempts exhausted after {trigger}",
            )
            return SendOutcome(
                failed, sent=False,
                reason=f"gave up after {order.send_attempts} attempts",
            )

        # Back to QUEUED via the broker's own verdict that nothing exists.
        self._emit(
            coid, EventKind.RECONCILED,
            reason="broker has no such order; safe to resend",
            payload={"state": OrderState.QUEUED.value},
        )
        return self.send(coid)

    # -- fills ------------------------------------------------------------

    def on_fill(
        self, coid: str, broker_event_id: str, quantity: Decimal, price: Decimal
    ) -> Order:
        """Apply an execution report.

        Idempotent by ``broker_event_id``: a redelivered report changes nothing.
        At-least-once delivery plus dedup here is how exactly-once is achieved
        logically, without needing it from the transport (ADR-EXE-4).
        """
        order = self.order(coid)
        if broker_event_id in order.seen_broker_events:
            return order

        would_total = order.filled_quantity + quantity
        if would_total > order.request.quantity:
            # The broker says we hold more than we asked for. That is not
            # arithmetic to reconcile in code -- it is a discrepancy a human
            # needs to see, and meanwhile the safe thing is to stop.
            self.dlq.add(
                OrderEvent(
                    kind=EventKind.FILLED, client_order_id=coid,
                    at_ns=self.clock.now_ns(), broker_event_id=broker_event_id,
                    quantity=quantity, price=price,
                ),
                reason=f"fill would take filled quantity to {would_total} on an "
                       f"order for {order.request.quantity}",
                at_ns=self.clock.now_ns(),
            )
            self.halt(f"over-fill on {coid}: broker reports more than ordered")
            return order

        kind = (
            EventKind.FILLED
            if would_total >= order.request.quantity
            else EventKind.PARTIALLY_FILLED
        )
        return self._emit(
            coid, kind, broker_event_id=broker_event_id,
            quantity=quantity, price=price,
        )

    # -- fail-safe --------------------------------------------------------

    def halt(self, reason: str) -> None:
        """Stop sending. Do not close anything.

        The distinction matters: closing open positions during an outage means
        trading blind at the worst possible moment. Halting means no *new* risk
        is taken while the situation is unclear.
        """
        if not reason.strip():
            raise DeterminismError("a halt without a reason cannot be reviewed")
        self._halted = True
        self._halt_reason = reason

    def resume(self, reason: str = "resumed") -> None:
        self._halted = False
        self._halt_reason = ""
        for order in self.orders:
            if order.state is OrderState.SAFE_HALT:
                self._emit(order.client_order_id, EventKind.RESUMED, reason=reason)

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    # -- recovery ---------------------------------------------------------

    def recover(self) -> list[SendOutcome]:
        """After a restart: reconcile everything the broker might still be acting on.

        The rule from ENG-006 §0.1: never guess the broker's state. Every order
        that could still be live gets asked about, one at a time.
        """
        outcomes = []
        for order in self.orders:
            if order.state.is_live_at_broker:
                outcomes.append(
                    self.reconcile(order.client_order_id, trigger="restart recovery")
                )
        return outcomes

    @classmethod
    def restore(
        cls,
        store: EventStore,
        adapter: BrokerAdapter,
        clock: Clock,
        **kwargs,
    ) -> "Oms":
        """Rebuild an OMS from a durable log.

        This is the other half of the promise event sourcing makes. The restored
        instance is not "close to" what died -- every order is folded from the
        same events in the same order, so it *is* what died, minus whatever was
        mid-flight. Call :meth:`recover` next to find out what the broker did
        while the process was gone.
        """
        loaded = store.load()
        oms = cls(adapter=adapter, clock=clock, store=store, **kwargs)
        oms._requests = dict(loaded.requests)
        oms._log = {coid: list(log) for coid, log in loaded.events.items()}
        oms._sequence = loaded.last_sequence

        # Rebuilding proves the log folds cleanly. Discovering it does not, on
        # the first order that needs acting on, would be much worse.
        loaded.rebuild()
        return oms

    @property
    def restored_orders(self) -> int:
        return len(self._requests)

    def health(self) -> str:
        exposed = self.exposed_orders()
        lines = [
            f"OMS {'HALTED: ' + self._halt_reason if self._halted else 'running'}",
            f"  orders        {len(self._log)} "
            f"({len(self.open_orders())} open, {len(exposed)} with exposure)",
            f"  events        {self._sequence}",
            f"  {self.breaker.describe()}",
            f"  outbox        {len(self.outbox)} pending, "
            f"{len(self.outbox.published)} published",
            f"  dead letters  {len(self.dlq)}",
        ]
        return "\n".join(lines)
