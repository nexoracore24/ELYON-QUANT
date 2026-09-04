"""Driving a session from a live feed.

Everything until now ran a session over a file, start to finish, on one thread.
Live changes two things, and the second is easy to miss:

**Ticks arrive instead of being iterated.** A feed can stall, repeat itself, or
stop. None of those are exceptional -- they are Tuesday -- so the runner treats
them as ordinary states with names rather than as errors.

**Two threads now touch the session.** The feed mutates it; the control surface
reads it to answer a phone. That is a data race, and the symptom would be a
snapshot describing a state that never existed -- a position half-written, a
candle list mid-append. Every mutation and every read goes through one lock.

The failure posture is the same as everywhere else in this system: when the feed
dies, stop opening new risk and keep reporting. A silent runner is
indistinguishable from a dead one, and the difference matters at 3am.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Protocol

from elyon.modules.market_data.domain import Tick
from elyon.shared_kernel.edcs.numeric import DeterminismError

from .runner import BarOutcome, TradingSession

SECOND_NS = 1_000_000_000


class FeedState(str, Enum):
    """What the feed is doing, as a state rather than an exception."""

    STARTING = "STARTING"
    LIVE = "LIVE"
    STALLED = "STALLED"      # connected, but nothing has arrived
    DISCONNECTED = "DISCONNECTED"
    STOPPED = "STOPPED"      # asked to stop

    @property
    def is_healthy(self) -> bool:
        return self is FeedState.LIVE


class TickFeed(Protocol):
    """A source of ticks.

    ``poll`` returns whatever has arrived since the last call, and an empty
    result is a normal answer -- markets go quiet. Raising is reserved for
    "the connection is gone".
    """

    def poll(self) -> list[Tick]: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class LiveConfig:
    poll_interval_seconds: float = 0.25
    # No tick for this long means something is wrong. Long enough to survive a
    # quiet market, short enough to notice a dead socket before a session ends.
    stall_after_seconds: float = 60.0
    # Halt the OMS when the feed dies. On by default: an engine holding a
    # position it can no longer see prices for should not be opening more.
    halt_on_disconnect: bool = True

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0:
            raise DeterminismError("poll interval must be positive")
        if self.stall_after_seconds <= self.poll_interval_seconds:
            raise DeterminismError(
                f"stall threshold ({self.stall_after_seconds}s) must exceed the "
                f"poll interval ({self.poll_interval_seconds}s), or the feed "
                f"would be declared stalled between two polls"
            )


@dataclass(slots=True)
class LiveRunner:
    """Feeds a session from a live source, on its own thread.

    The session is only ever touched while holding :attr:`lock`. Both this
    class and anything reading the session -- the control surface, most
    obviously -- must go through :meth:`read`.
    """

    session: TradingSession
    feed: TickFeed
    config: LiveConfig = field(default_factory=LiveConfig)

    lock: threading.RLock = field(default_factory=threading.RLock)
    state: FeedState = FeedState.STARTING
    detail: str = ""
    ticks_seen: int = 0
    bars_built: int = 0
    last_tick_at: float = 0.0
    # Feeds repeat themselves when nothing has changed; folding the same tick
    # twice would inflate volume and tick counts on a candle.
    _last_signature: tuple | None = None
    _thread: threading.Thread | None = None
    _stopping: threading.Event = field(default_factory=threading.Event)
    on_outcome: Callable[[BarOutcome], None] | None = None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise DeterminismError("this runner has already been started")
        self._stopping.clear()
        self.last_tick_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._loop, name="elyon-live", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        with self.lock:
            self.state = FeedState.STOPPED
        self.feed.close()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- the loop ---------------------------------------------------------

    def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                ticks = self.feed.poll()
            except Exception as exc:  # noqa: BLE001 -- a feed raises anything
                self._on_disconnect(str(exc))
                self._stopping.wait(self.config.poll_interval_seconds)
                continue

            if ticks:
                self._absorb(ticks)
            else:
                self._check_stall()

            self._stopping.wait(self.config.poll_interval_seconds)

    def _absorb(self, ticks: list[Tick]) -> None:
        now = time.monotonic()
        produced: list[BarOutcome] = []

        with self.lock:
            for tick in ticks:
                signature = (tick.event_time_ns, tick.bid, tick.ask)
                if signature == self._last_signature:
                    continue  # the feed repeated itself
                self._last_signature = signature
                self.ticks_seen += 1
                produced.extend(self.session.on_tick(tick))

            self.bars_built += len(produced)
            self.last_tick_at = now
            self.state = FeedState.LIVE
            self.detail = ""

        # Callbacks run outside the lock. A slow notifier holding it would
        # stall the feed, and a feed that stops reading is a feed that misses
        # the bar it was waiting for.
        for outcome in produced:
            if self.on_outcome is not None:
                self.on_outcome(outcome)

    def _check_stall(self) -> None:
        silent_for = time.monotonic() - self.last_tick_at
        if silent_for < self.config.stall_after_seconds:
            return
        with self.lock:
            if self.state is not FeedState.STALLED:
                self.state = FeedState.STALLED
                self.detail = (
                    f"no tick for {int(silent_for)}s. The market may be closed, "
                    f"or the connection may be gone -- from here they look the "
                    f"same."
                )

    def _on_disconnect(self, message: str) -> None:
        with self.lock:
            already = self.state is FeedState.DISCONNECTED
            self.state = FeedState.DISCONNECTED
            self.detail = message
            # An engine that cannot see prices should not be opening new risk.
            # It keeps whatever it holds: closing blind during an outage is
            # trading at the worst possible moment.
            if self.config.halt_on_disconnect and not self.session.oms.is_halted:
                self.session.oms.halt(f"market data feed lost: {message}")
            if not already:
                print(f"feed disconnected: {message}")

    # -- reading ----------------------------------------------------------

    def read(self, view: Callable[[TradingSession], Any]) -> Any:
        """Look at the session safely.

        Everything outside this thread reads through here. Without it, a
        snapshot taken mid-append describes a state that never existed.
        """
        with self.lock:
            return view(self.session)

    def mutate(self, action: Callable[[TradingSession], Any]) -> Any:
        """Change the session safely.

        The same lock as :meth:`read`, under a name that says what the caller
        is doing. Halting, resuming and reconfiguring all arrive from the
        control surface while the feed thread is mid-bar, and a configuration
        swapped halfway through an evaluation is a bar decided under two
        different sets of rules.
        """
        with self.lock:
            return action(self.session)

    def health(self) -> Mapping[str, Any]:
        with self.lock:
            return {
                "feed": self.state.value,
                "feedDetail": self.detail,
                "feedHealthy": self.state.is_healthy,
                "ticks": self.ticks_seen,
                "barsBuilt": self.bars_built,
                "secondsSinceTick": round(
                    time.monotonic() - self.last_tick_at, 1
                ),
            }


# ---------------------------------------------------------------------------
# A feed for tests and dry runs
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ReplayFeed:
    """Replays a fixed list of ticks, then reports the stream as ended.

    Useful for exercising the live path without a broker: the same runner, the
    same locking, the same disconnect handling -- only the source differs.
    """

    ticks: list[Tick]
    batch: int = 10
    fail_after: int | None = None
    _index: int = 0
    _closed: bool = False

    def poll(self) -> list[Tick]:
        if self._closed:
            raise ConnectionError("feed closed")
        if self.fail_after is not None and self._index >= self.fail_after:
            raise ConnectionError("simulated disconnect")
        if self._index >= len(self.ticks):
            return []
        chunk = self.ticks[self._index : self._index + self.batch]
        self._index += len(chunk)
        return chunk

    def close(self) -> None:
        self._closed = True

    @property
    def exhausted(self) -> bool:
        return self._index >= len(self.ticks)
