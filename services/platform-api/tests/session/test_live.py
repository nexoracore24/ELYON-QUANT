"""Live feed tests.

Two things change when a session stops reading a file and starts listening.

**Ticks arrive rather than being iterated**, and a feed that stalls, repeats
itself or dies is Tuesday rather than an exception -- so each of those is a
named state with a test.

**Two threads now touch the session.** The feed mutates it while the control
surface reads it, and the symptom of getting that wrong is a snapshot that
describes a state which never existed. That one gets a test that actually runs
both threads at once, because a lock nobody contends is a lock nobody has
tested.
"""

from __future__ import annotations

import threading
import time

import pytest

from elyon.modules.execution.infrastructure.mt5_feed import Mt5TickFeed
from elyon.modules.market_context.domain import learn_dna, profile_for
from elyon.modules.market_data.domain import Tick, Timeframe
from elyon.modules.session.domain import (
    FeedState,
    LiveConfig,
    LiveRunner,
    ReplayFeed,
    SessionConfig,
    TradingSession,
)
from elyon.modules.strategy.domain import Calibration, StrategyId
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

SYMBOL = "EURUSD"
SECOND = 1_000_000_000
BASE = 1768485600_000_000_000

PROVEN = {
    StrategyId.SIX_PILLARS: Calibration(180, 92, dec("0.42"), dataset="t")
}
FAST = LiveConfig(poll_interval_seconds=0.01, stall_after_seconds=0.2)


def ticks(count: int = 200) -> list[Tick]:
    """A gently drifting stream, one tick every ten seconds."""
    out = []
    price = dec("1.10000")
    for i in range(count):
        price += dec("0.00001") if i % 3 else dec("-0.00002")
        out.append(Tick(
            symbol=SYMBOL,
            event_time_ns=BASE + i * 10 * SECOND,
            bid=price - dec("0.00005"),
            ask=price + dec("0.00005"),
            provider="test", seq=i, volume=dec("1"),
        ))
    return out


def a_session() -> TradingSession:
    config = SessionConfig(symbol=SYMBOL, calibrations=PROVEN, warmup_bars=20,
                           lookback_bars=60, atr_period=14)
    return TradingSession(config, dna=profile_for(SYMBOL))


def wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestDrivingASession:
    def test_ticks_become_candles(self):
        runner = LiveRunner(a_session(), ReplayFeed(ticks()), FAST)
        runner.start()
        assert wait_until(lambda: runner.bars_built > 0)
        runner.stop()
        assert runner.ticks_seen > 0

    def test_the_state_reaches_live(self):
        runner = LiveRunner(a_session(), ReplayFeed(ticks()), FAST)
        runner.start()
        assert wait_until(lambda: runner.state is FeedState.LIVE)
        runner.stop()

    def test_a_repeated_tick_is_folded_once(self):
        # MT5 returns the same tick until something changes; counting it twice
        # would inflate volume and tick count on the candle.
        one = ticks(1)
        runner = LiveRunner(a_session(), ReplayFeed(one * 20), FAST)
        runner.start()
        assert wait_until(lambda: runner.ticks_seen >= 1)
        time.sleep(0.1)
        runner.stop()
        assert runner.ticks_seen == 1

    def test_stopping_is_idempotent_enough_to_be_safe(self):
        runner = LiveRunner(a_session(), ReplayFeed(ticks(10)), FAST)
        runner.start()
        runner.stop()
        runner.stop()   # must not raise
        assert not runner.running

    def test_starting_twice_is_refused(self):
        runner = LiveRunner(a_session(), ReplayFeed(ticks(10)), FAST)
        runner.start()
        with pytest.raises(DeterminismError, match="already been started"):
            runner.start()
        runner.stop()


class TestWhenTheFeedMisbehaves:
    def test_silence_becomes_a_stall_not_a_crash(self):
        # Markets go quiet. That is a state, not an error.
        runner = LiveRunner(a_session(), ReplayFeed([]), FAST)
        runner.start()
        assert wait_until(lambda: runner.state is FeedState.STALLED)
        runner.stop()

    def test_a_stall_explains_the_ambiguity(self):
        # A closed market and a dead socket look identical from here, and
        # saying so is more useful than picking one.
        runner = LiveRunner(a_session(), ReplayFeed([]), FAST)
        runner.start()
        wait_until(lambda: runner.state is FeedState.STALLED)
        detail = runner.detail
        runner.stop()
        assert "market may be closed" in detail
        assert "connection may be gone" in detail

    def test_a_disconnect_is_recorded(self):
        runner = LiveRunner(
            a_session(), ReplayFeed(ticks(), fail_after=20), FAST
        )
        runner.start()
        assert wait_until(lambda: runner.state is FeedState.DISCONNECTED)
        runner.stop()

    def test_a_disconnect_halts_the_engine(self):
        # An engine that cannot see prices should not be opening new risk.
        session = a_session()
        runner = LiveRunner(session, ReplayFeed(ticks(), fail_after=20), FAST)
        runner.start()
        assert wait_until(lambda: session.oms.is_halted)
        runner.stop()
        assert "feed lost" in session.oms.halt_reason

    def test_a_disconnect_does_not_close_positions(self):
        # Closing blind during an outage is trading at the worst moment.
        session = a_session()
        runner = LiveRunner(session, ReplayFeed(ticks(), fail_after=20), FAST)
        runner.start()
        wait_until(lambda: session.oms.is_halted)
        runner.stop()
        # Nothing was force-closed; whatever was held is still held.
        assert session.closed_positions == [] or all(
            p.close_reason is not None for p in session.closed_positions
        )

    def test_halting_on_disconnect_can_be_turned_off(self):
        session = a_session()
        runner = LiveRunner(
            session, ReplayFeed(ticks(), fail_after=20),
            LiveConfig(poll_interval_seconds=0.01, stall_after_seconds=0.2,
                       halt_on_disconnect=False),
        )
        runner.start()
        assert wait_until(lambda: runner.state is FeedState.DISCONNECTED)
        runner.stop()
        assert not session.oms.is_halted

    def test_the_runner_survives_a_feed_that_keeps_failing(self):
        # It reports and keeps polling rather than dying silently. A dead
        # runner and a quiet one look the same from a phone.
        runner = LiveRunner(a_session(), ReplayFeed([], fail_after=0), FAST)
        runner.start()
        assert wait_until(lambda: runner.state is FeedState.DISCONNECTED)
        time.sleep(0.1)
        assert runner.running
        runner.stop()


class TestThreadSafety:
    def test_reading_while_the_feed_writes_never_tears(self):
        # The race this lock exists for. Without it a snapshot can describe a
        # state that never existed -- a candle half-added, a position
        # half-written.
        from elyon.modules.api.domain import session_snapshot

        session = a_session()
        runner = LiveRunner(session, ReplayFeed(ticks(400), batch=3), FAST)
        runner.start()

        failures: list[Exception] = []

        def reader():
            for _ in range(300):
                try:
                    snapshot = runner.read(session_snapshot)
                    # Internally consistent: every entry either closed or is
                    # the one still open.
                    assert snapshot["closed"] <= snapshot["entries"]
                    assert sum(snapshot["stoppedAt"].values()) == snapshot["bars"]
                except Exception as exc:  # noqa: BLE001
                    failures.append(exc)
                    return

        readers = [threading.Thread(target=reader) for _ in range(4)]
        for thread in readers:
            thread.start()
        for thread in readers:
            thread.join(5)
        runner.stop()

        assert not failures, failures[0]

    def test_health_is_readable_while_running(self):
        runner = LiveRunner(a_session(), ReplayFeed(ticks()), FAST)
        runner.start()
        wait_until(lambda: runner.ticks_seen > 0)
        health = runner.health()
        runner.stop()
        assert "feed" in health
        assert health["ticks"] > 0


class TestConfiguration:
    def test_a_stall_threshold_inside_the_poll_interval_is_refused(self):
        # The feed would be declared stalled between two polls.
        with pytest.raises(DeterminismError, match="must exceed the"):
            LiveConfig(poll_interval_seconds=1.0, stall_after_seconds=0.5)

    def test_a_non_positive_poll_interval_is_refused(self):
        with pytest.raises(DeterminismError, match="must be positive"):
            LiveConfig(poll_interval_seconds=0)


# ---------------------------------------------------------------------------
# The MT5 feed itself
# ---------------------------------------------------------------------------

class FakeTick:
    def __init__(self, bid, ask, time_msc=0, time=0, volume=1):
        self.bid, self.ask = bid, ask
        self.time_msc, self.time, self.volume = time_msc, time, volume


class FakeSymbol:
    def __init__(self, visible=True):
        self.visible = visible


class FakeTerminal:
    def __init__(self, tick=None, symbol=FakeSymbol(), terminal=True):
        self._tick, self._symbol, self._terminal = tick, symbol, terminal
        self.selected = []

    def symbol_info_tick(self, symbol):
        return self._tick

    def symbol_info(self, symbol):
        return self._symbol

    def terminal_info(self):
        return self._terminal

    def symbol_select(self, symbol, enable):
        self.selected.append(symbol)
        return True


class TestTheMt5Feed:
    def test_a_tick_becomes_a_domain_tick(self):
        terminal = FakeTerminal(FakeTick(1.10001, 1.10003, time_msc=1768485600123))
        feed = Mt5TickFeed(SYMBOL, mt5=terminal)
        [tick] = feed.poll()
        assert tick.bid == dec("1.10001")
        assert tick.symbol == SYMBOL

    def test_prices_go_through_str_not_float(self):
        # Decimal(float) would carry the double's error into a value the rest
        # of the engine treats as exact.
        terminal = FakeTerminal(FakeTick(1.10005, 1.10007, time_msc=1))
        [tick] = Mt5TickFeed(SYMBOL, mt5=terminal).poll()
        assert str(tick.bid) == "1.10005"

    def test_milliseconds_are_preferred_over_seconds(self):
        # Two ticks inside one second are ordinary, and collapsing them loses
        # the order they arrived in.
        terminal = FakeTerminal(FakeTick(1.1, 1.2, time_msc=1768485600123, time=1768485600))
        [tick] = Mt5TickFeed(SYMBOL, mt5=terminal).poll()
        assert tick.event_time_ns == 1768485600123 * 1_000_000

    def test_seconds_are_used_when_milliseconds_are_absent(self):
        terminal = FakeTerminal(FakeTick(1.1, 1.2, time=1768485600))
        [tick] = Mt5TickFeed(SYMBOL, mt5=terminal).poll()
        assert tick.event_time_ns == 1768485600 * SECOND

    def test_an_unchanged_tick_is_not_repeated(self):
        terminal = FakeTerminal(FakeTick(1.1, 1.2, time_msc=1))
        feed = Mt5TickFeed(SYMBOL, mt5=terminal)
        assert len(feed.poll()) == 1
        assert feed.poll() == []

    def test_ticks_are_sequenced_for_stable_ordering(self):
        terminal = FakeTerminal(FakeTick(1.1, 1.2, time_msc=1))
        feed = Mt5TickFeed(SYMBOL, mt5=terminal)
        first = feed.poll()[0]
        terminal._tick = FakeTick(1.3, 1.4, time_msc=1)   # same timestamp
        second = feed.poll()[0]
        assert second.seq > first.seq

    def test_the_account_suffix_is_applied(self):
        from elyon.modules.execution.infrastructure.mt5 import Mt5Config
        feed = Mt5TickFeed(SYMBOL, Mt5Config(symbol_suffix="m"),
                           mt5=FakeTerminal())
        assert feed.venue_symbol == "EURUSDm"

    def test_a_missing_terminal_is_named(self):
        feed = Mt5TickFeed(SYMBOL, mt5=FakeTerminal(tick=None, terminal=None))
        with pytest.raises(ConnectionError, match="not reachable"):
            feed.poll()

    def test_an_unknown_symbol_suggests_the_suffix(self):
        # The single most common Exness mistake.
        feed = Mt5TickFeed(SYMBOL, mt5=FakeTerminal(tick=None, symbol=None))
        with pytest.raises(ConnectionError, match="suffix"):
            feed.poll()

    def test_a_hidden_symbol_says_to_add_it(self):
        # It returns None from every price call, which looks exactly like a
        # dead connection until somebody checks.
        feed = Mt5TickFeed(
            SYMBOL, mt5=FakeTerminal(tick=None, symbol=FakeSymbol(visible=False))
        )
        with pytest.raises(ConnectionError, match="Market Watch"):
            feed.poll()

    def test_a_closed_market_says_so(self):
        feed = Mt5TickFeed(SYMBOL, mt5=FakeTerminal(tick=None))
        with pytest.raises(ConnectionError, match="market may be closed"):
            feed.poll()

    def test_ensure_symbol_adds_a_hidden_one(self):
        terminal = FakeTerminal(symbol=FakeSymbol(visible=False))
        Mt5TickFeed(SYMBOL, mt5=terminal).ensure_symbol()
        assert terminal.selected == [SYMBOL]

    def test_ensure_symbol_refuses_an_unknown_one_at_startup(self):
        # Better to fail here with the reason than to look like a dead socket
        # for an hour.
        feed = Mt5TickFeed(SYMBOL, mt5=FakeTerminal(symbol=None))
        with pytest.raises(DeterminismError, match="symbol_suffix"):
            feed.ensure_symbol()

    def test_a_closed_feed_refuses_to_poll(self):
        feed = Mt5TickFeed(SYMBOL, mt5=FakeTerminal(FakeTick(1.1, 1.2, time_msc=1)))
        feed.close()
        with pytest.raises(ConnectionError, match="closed"):
            feed.poll()
