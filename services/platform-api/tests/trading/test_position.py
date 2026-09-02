"""Position management tests.

One rule dominates: **a stop never moves against the position.** A "trailing"
stop that can widen is not a trailing stop, it is a stop somebody moved because
they did not like being wrong, and it turns a bounded loss into an unbounded
one. Every path that produces a stop is tested against that.

Everything else is measured in R, so the same rules are tested once and hold for
a 5-pip stop on EURUSD and a $30 stop on gold.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from elyon.modules.market_data.domain.model import Candle, CandleState, Timeframe
from elyon.modules.smart_money.domain.structure import Direction
from elyon.modules.trading.domain.position import (
    CloseReason,
    ManagedPosition,
    ManagementAction,
    ManagementPolicy,
    manage,
    open_position,
)
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec

SYMBOL = "EURUSD"
M1 = Timeframe.M1
ATR = dec("0.0010")

# Entry 1.1000, stop 1.0990 -> 1R is 10 pips. Every price below is chosen so the
# R it represents is obvious by inspection.
ENTRY = dec("1.1000")
STOP = dec("1.0990")
TARGET = dec("1.1030")
RISK = dec("0.0010")


def bar(i: int, o: str, h: str, l: str, c: str) -> Candle:
    op, cl = dec(o), dec(c)
    return Candle(
        symbol=SYMBOL, timeframe=M1,
        open_time_ns=i * M1.duration_ns,
        close_time_ns=(i + 1) * M1.duration_ns,
        open=op, high=max(dec(h), op, cl), low=min(dec(l), op, cl), close=cl,
        volume=dec("10"), tick_count=4, state=CandleState.CONFIRMED,
    )


def at_r(multiple: str, direction: Direction = Direction.UP) -> str:
    """The price that is exactly ``multiple`` R from entry."""
    sign = dec(1) if direction is Direction.UP else dec(-1)
    return str(ENTRY + RISK * dec(multiple) * sign)


def long_position(**kwargs) -> ManagedPosition:
    settings = dict(
        symbol=SYMBOL, direction=Direction.UP, entry=ENTRY, stop=STOP,
        target=TARGET, quantity=dec("1.0"), at_index=0,
    )
    settings.update(kwargs)
    return open_position(**settings)


def short_position(**kwargs) -> ManagedPosition:
    settings = dict(
        symbol=SYMBOL, direction=Direction.DOWN, entry=ENTRY,
        stop=dec("1.1010"), target=dec("1.0970"),
        quantity=dec("1.0"), at_index=0,
    )
    settings.update(kwargs)
    return open_position(**settings)


class TestTheStopNeverMovesBackwards:
    """The rule that keeps a bounded loss bounded."""

    def test_widening_a_long_stop_is_refused(self):
        position = long_position()
        with pytest.raises(DeterminismError, match="widens the risk"):
            position.with_stop(dec("1.0980"), "panic")

    def test_widening_a_short_stop_is_refused(self):
        position = short_position()
        with pytest.raises(DeterminismError, match="widens the risk"):
            position.with_stop(dec("1.1020"), "panic")

    def test_leaving_it_where_it_is_is_also_refused(self):
        # Not an error to guard against so much as a signal: a "move" that
        # changes nothing means the caller's logic did not do what it thought.
        position = long_position()
        with pytest.raises(DeterminismError, match="refusing to move"):
            position.with_stop(position.stop, "no-op")

    def test_tightening_is_allowed(self):
        position = long_position().with_stop(dec("1.0995"), "trail")
        assert position.stop == dec("1.0995")

    def test_the_trail_never_loosens_across_a_pullback(self):
        # Price runs, then pulls back. The trail must hold where it was.
        rules = ManagementPolicy(break_even_at_r=None, trail_from_r=dec("1.0"),
                                 trail_distance_atr=dec("1.0"), partial_at_r=None)
        position = long_position()

        after_run = manage(position, bar(1, at_r("0.5"), at_r("2.0"),
                                         at_r("0.4"), at_r("1.8")), ATR, policy=rules)
        trailed = after_run.position.stop

        after_pullback = manage(
            after_run.position,
            bar(2, at_r("1.8"), at_r("1.9"), at_r("1.2"), at_r("1.3")),
            ATR, policy=rules,
        )
        assert after_pullback.position.stop == trailed

    def test_every_managed_bar_leaves_the_stop_no_worse(self):
        # The property, swept over a run of bars rather than asserted once.
        position = long_position()
        rules = ManagementPolicy()
        previous = position.stop
        prices = ["0.5", "1.2", "0.9", "1.6", "1.4", "2.1", "1.7"]
        for i, r in enumerate(prices, start=1):
            candle = bar(i, at_r(r), at_r(r), at_r(r), at_r(r))
            decision = manage(position, candle, ATR, policy=rules)
            position = decision.position
            if position.closed:
                break
            assert position.stop >= previous
            previous = position.stop


class TestBreakEven:
    def test_it_triggers_at_the_configured_r(self):
        rules = ManagementPolicy(break_even_at_r=dec("1.0"), partial_at_r=None,
                                 trail_from_r=None)
        decision = manage(
            long_position(),
            bar(1, at_r("0.5"), at_r("1.1"), at_r("0.4"), at_r("1.0")),
            ATR, policy=rules,
        )
        assert decision.action is ManagementAction.MOVE_STOP
        assert decision.position.broke_even

    def test_it_does_not_trigger_early(self):
        rules = ManagementPolicy(break_even_at_r=dec("1.0"), partial_at_r=None,
                                 trail_from_r=None)
        decision = manage(
            long_position(),
            bar(1, at_r("0.3"), at_r("0.8"), at_r("0.2"), at_r("0.7")),
            ATR, policy=rules,
        )
        assert decision.action is ManagementAction.HOLD
        assert decision.position.stop == STOP

    def test_the_stop_lands_beyond_entry_not_on_it(self):
        # A stop exactly at entry still loses the round turn, so "break-even"
        # that does not cover costs is a small loss wearing the name of scratch.
        rules = ManagementPolicy(
            break_even_at_r=dec("1.0"), break_even_buffer_r=dec("0.1"),
            partial_at_r=None, trail_from_r=None,
        )
        decision = manage(
            long_position(),
            bar(1, at_r("0.5"), at_r("1.2"), at_r("0.4"), at_r("1.1")),
            ATR, policy=rules,
        )
        assert decision.position.stop > ENTRY
        assert decision.position.locked_r() > ZERO
        assert decision.position.is_risk_free

    def test_it_works_the_same_way_short(self):
        rules = ManagementPolicy(break_even_at_r=dec("1.0"), partial_at_r=None,
                                 trail_from_r=None)
        # at_r already carries the direction's sign, so a positive multiple is
        # the favourable side for either book.
        down = Direction.DOWN
        decision = manage(
            short_position(),
            bar(1, at_r("0.5", down), at_r("0.4", down),
                at_r("1.2", down), at_r("1.1", down)),
            ATR, policy=rules,
        )
        assert decision.position.stop < ENTRY
        assert decision.position.is_risk_free

    def test_it_can_be_switched_off(self):
        # Some strategies are hurt by it: a stop at entry sits exactly where
        # price likes to retest.
        rules = ManagementPolicy(break_even_at_r=None, partial_at_r=None,
                                 trail_from_r=None)
        decision = manage(
            long_position(),
            bar(1, at_r("1.0"), at_r("1.5"), at_r("0.9"), at_r("1.4")),
            ATR, policy=rules,
        )
        assert decision.position.stop == STOP


class TestPartials:
    def test_a_partial_is_banked_at_the_trigger_not_the_close(self):
        # The order would have been resting at the level and filled on the way
        # through. Crediting the close would book whatever the bar did after.
        rules = ManagementPolicy(
            partial_at_r=dec("1.5"), partial_fraction=dec("0.5"),
            break_even_at_r=None, trail_from_r=None,
        )
        decision = manage(
            long_position(),
            bar(1, at_r("1.0"), at_r("2.0"), at_r("0.9"), at_r("1.0")),
            ATR, policy=rules,
        )
        assert decision.action is ManagementAction.TAKE_PARTIAL
        # Half the position at 1.5R = 0.75R realized, not whatever 1.0R close.
        assert decision.position.realized_r == dec("0.75")

    def test_it_reduces_the_open_quantity(self):
        rules = ManagementPolicy(partial_at_r=dec("1.5"),
                                 partial_fraction=dec("0.5"),
                                 break_even_at_r=None, trail_from_r=None)
        decision = manage(
            long_position(),
            bar(1, at_r("1.0"), at_r("2.0"), at_r("0.9"), at_r("1.6")),
            ATR, policy=rules,
        )
        assert decision.position.open_quantity == dec("0.5")
        assert decision.close_quantity == dec("0.5")

    def test_it_only_happens_once(self):
        rules = ManagementPolicy(partial_at_r=dec("1.5"),
                                 partial_fraction=dec("0.5"),
                                 break_even_at_r=None, trail_from_r=None)
        first = manage(
            long_position(),
            bar(1, at_r("1.0"), at_r("2.0"), at_r("0.9"), at_r("1.6")),
            ATR, policy=rules,
        )
        second = manage(
            first.position,
            bar(2, at_r("1.6"), at_r("2.2"), at_r("1.5"), at_r("2.0")),
            ATR, policy=rules,
        )
        assert second.action is not ManagementAction.TAKE_PARTIAL
        assert second.position.open_quantity == dec("0.5")

    def test_an_invalid_fraction_is_refused(self):
        with pytest.raises(DeterminismError, match="outside"):
            ManagementPolicy(partial_fraction=dec("1.5"))


class TestExits:
    def test_a_bar_holding_both_levels_resolves_as_a_stop(self):
        # Same rule as the backtester: OHLC cannot say which came first, so the
        # unfavourable assumption is the only safe one.
        decision = manage(
            long_position(),
            bar(1, at_r("0.5"), at_r("3.5"), at_r("-1.5"), at_r("1.0")),
            ATR,
        )
        assert decision.action is ManagementAction.CLOSE
        assert decision.position.close_reason is CloseReason.STOP_HIT

    def test_a_clean_target_bar_closes_at_the_target(self):
        decision = manage(
            long_position(),
            bar(1, at_r("1.0"), at_r("3.5"), at_r("0.9"), at_r("3.0")),
            ATR,
        )
        assert decision.position.close_reason is CloseReason.TARGET_HIT
        assert decision.position.realized_r == dec("3")

    def test_a_stop_out_books_exactly_minus_one_r(self):
        decision = manage(
            long_position(),
            bar(1, at_r("-0.2"), at_r("0.1"), at_r("-1.4"), at_r("-1.2")),
            ATR,
        )
        assert decision.position.realized_r == dec("-1")

    def test_a_trade_going_nowhere_is_closed(self):
        # Capital tied up in a trade that is not working is capital unavailable
        # to one that is.
        rules = ManagementPolicy(time_stop_bars=3, time_stop_min_r=dec("0.5"),
                                 break_even_at_r=None, trail_from_r=None,
                                 partial_at_r=None)
        position = long_position()
        for i in range(1, 4):
            decision = manage(
                position, bar(i, at_r("0.1"), at_r("0.2"), at_r("-0.1"), at_r("0.1")),
                ATR, policy=rules,
            )
            position = decision.position
        assert decision.position.close_reason is CloseReason.TIME_STOP

    def test_a_trade_that_did_work_is_not_time_stopped(self):
        rules = ManagementPolicy(time_stop_bars=2, time_stop_min_r=dec("0.5"),
                                 break_even_at_r=None, trail_from_r=None,
                                 partial_at_r=None)
        position = long_position()
        for i in range(1, 4):
            decision = manage(
                position, bar(i, at_r("0.8"), at_r("0.9"), at_r("0.7"), at_r("0.8")),
                ATR, policy=rules,
            )
            position = decision.position
        assert not decision.position.closed

    def test_a_closed_position_stops_being_managed(self):
        closed = manage(
            long_position(),
            bar(1, at_r("0"), at_r("0.1"), at_r("-1.5"), at_r("-1.4")),
            ATR,
        ).position
        again = manage(closed, bar(2, at_r("2"), at_r("3"), at_r("1"), at_r("2")), ATR)
        assert again.action is ManagementAction.HOLD
        assert again.position.realized_r == closed.realized_r


class TestMeasurement:
    def test_r_is_never_rebased_after_the_stop_moves(self):
        # Rebasing would make a trade look better simply because it was
        # managed: 1R would keep shrinking and every result would inflate.
        position = long_position()
        moved = position.with_stop(dec("1.0998"), "trail")
        assert moved.initial_risk == position.initial_risk
        assert moved.r_at(dec("1.1010")) == dec("1")

    def test_locked_r_reports_the_worst_still_possible(self):
        position = long_position()
        assert position.locked_r() == dec("-1")
        assert position.with_stop(ENTRY, "be").locked_r() == ZERO

    def test_a_position_with_no_risk_is_refused(self):
        with pytest.raises(DeterminismError, match="no defined risk"):
            open_position(
                symbol=SYMBOL, direction=Direction.UP, entry=ENTRY,
                stop=ENTRY, target=TARGET, quantity=dec("1"), at_index=0,
            )

    def test_a_long_with_the_stop_above_entry_is_refused(self):
        with pytest.raises(DeterminismError, match="at or above entry"):
            open_position(
                symbol=SYMBOL, direction=Direction.UP, entry=ENTRY,
                stop=dec("1.1010"), target=TARGET, quantity=dec("1"), at_index=0,
            )

    def test_a_short_with_the_stop_below_entry_is_refused(self):
        with pytest.raises(DeterminismError, match="at or below entry"):
            open_position(
                symbol=SYMBOL, direction=Direction.DOWN, entry=ENTRY,
                stop=dec("1.0990"), target=dec("1.0970"),
                quantity=dec("1"), at_index=0,
            )

    def test_the_journal_records_what_was_done(self):
        position = long_position().with_stop(dec("1.0995"), "trail")
        assert any("opened" in entry for entry in position.journal)
        assert any("trail" in entry for entry in position.journal)


class TestPolicyCoherence:
    def test_trailing_cannot_start_before_break_even(self):
        # It would move the stop backwards the moment break-even applies.
        with pytest.raises(DeterminismError, match="backwards from break-even"):
            ManagementPolicy(break_even_at_r=dec("1.5"), trail_from_r=dec("1.0"))

    def test_the_default_policy_is_coherent(self):
        ManagementPolicy()   # must not raise
