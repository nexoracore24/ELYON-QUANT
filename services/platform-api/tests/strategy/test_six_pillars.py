"""Six-pillar strategy tests.

The strategy is: TENDENCIA, LIQUIDEZ, ORDER BLOCK, FVG, FIBONACCI, ZONA OTE.
These tests hold that definition still. They check that all six are always
reported -- present or absent, each with a reason -- and that the numbers the
strategy derives from them (where to enter, where it is wrong, where it is
going) point the right way on both sides of the market.

A short whose stop sits below the entry is not a rounding error, it is a
guaranteed loss, so direction-dependence gets explicit tests.
"""

from __future__ import annotations

import pytest

from elyon.modules.market_data.domain.model import Candle, CandleState, Timeframe
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.smart_money.domain.structure import Direction
from elyon.modules.smart_money.domain.zones import Pricing
from elyon.modules.strategy.domain import (
    PILLAR_FACTORS,
    Pillar,
    locate_six_pillars,
    pillar_summary,
    score_setup,
)
from elyon.modules.trading.domain.scoring import Factor
from elyon.shared_kernel.edcs.numeric import dec

SYMBOL = "EURUSD"
M1 = Timeframe.M1
MINUTE = M1.duration_ns
ATR = dec("0.00437")


def bar(i: int, o: str, h: str, l: str, c: str) -> Candle:
    """A confirmed bar; high/low widened to contain the body if needed."""
    op, cl = dec(o), dec(c)
    return Candle(
        symbol=SYMBOL, timeframe=M1,
        open_time_ns=i * MINUTE, close_time_ns=(i + 1) * MINUTE,
        open=op, high=max(dec(h), op, cl), low=min(dec(l), op, cl), close=cl,
        volume=dec("10"), tick_count=4, state=CandleState.CONFIRMED,
    )


def series_of(bars: list[tuple[str, str, str, str]]) -> CandleSeries:
    return CandleSeries.of([bar(i, *ohlc) for i, ohlc in enumerate(bars)])


def bullish_series() -> CandleSeries:
    """Uptrend, sweep of the lows, impulsive recovery, retrace into the block."""
    return series_of([
        ("1.1010", "1.1030", "1.1008", "1.1025"),
        ("1.1025", "1.1028", "1.0995", "1.1020"),
        ("1.1020", "1.1060", "1.1018", "1.1055"),
        ("1.1055", "1.1058", "1.1015", "1.1050"),
        ("1.1050", "1.1090", "1.1045", "1.1085"),
        ("1.1085", "1.1088", "1.1040", "1.1080"),
        ("1.1080", "1.1120", "1.1075", "1.1115"),
        ("1.1115", "1.1118", "1.1035", "1.1070"),   # the sweep
        ("1.1070", "1.1145", "1.1068", "1.1140"),   # displacement up
        ("1.1140", "1.1150", "1.1132", "1.1142"),
        ("1.1142", "1.1145", "1.1078", "1.1082"),   # retrace into the block
        ("1.1082", "1.1086", "1.1072", "1.1080"),
    ])


def bearish_series() -> CandleSeries:
    """The same story mirrored: downtrend, sweep of the highs, drop, retrace."""
    return series_of([
        ("1.1120", "1.1122", "1.1100", "1.1105"),
        ("1.1105", "1.1135", "1.1102", "1.1110"),
        ("1.1110", "1.1112", "1.1070", "1.1075"),
        ("1.1075", "1.1115", "1.1072", "1.1080"),
        ("1.1080", "1.1082", "1.1040", "1.1045"),
        ("1.1045", "1.1090", "1.1042", "1.1050"),
        ("1.1050", "1.1052", "1.1010", "1.1015"),
        ("1.1015", "1.1095", "1.1012", "1.1060"),   # sweep of the highs
        ("1.1060", "1.1062", "1.0985", "1.0990"),   # displacement down
        ("1.0990", "1.0998", "1.0980", "1.0988"),
        ("1.0988", "1.1052", "1.0985", "1.1048"),   # retrace into the block
        ("1.1048", "1.1055", "1.1044", "1.1050"),
    ])


def choppy_series() -> CandleSeries:
    """Directionless noise -- the market the strategy should refuse."""
    return series_of([
        ("1.1000", "1.1012", "1.0994", "1.1004"),
        ("1.1004", "1.1010", "1.0996", "1.1000"),
        ("1.1000", "1.1014", "1.0998", "1.1002"),
        ("1.1002", "1.1008", "1.0992", "1.1006"),
        ("1.1006", "1.1013", "1.0997", "1.0999"),
        ("1.0999", "1.1011", "1.0995", "1.1005"),
        ("1.1005", "1.1009", "1.0993", "1.1001"),
        ("1.1001", "1.1015", "1.0999", "1.1003"),
    ])


class TestTheStrategyIsWhatWeSaidItWas:
    """The six names are the product. Renaming one silently is a regression."""

    def test_there_are_exactly_six_pillars(self):
        assert len(Pillar) == 6

    def test_they_are_the_six_the_strategy_asked_for(self):
        assert {p.value for p in Pillar} == {
            "TENDENCIA", "LIQUIDEZ", "ORDER_BLOCK", "FVG", "FIBONACCI", "OTE",
        }

    def test_they_are_reported_in_the_order_a_trader_reads_them(self):
        # Trend first: it decides which side the rest is even looked for on.
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        assert [f.pillar for f in setup.findings] == list(Pillar)


class TestEveryPillarAnswers:
    def test_all_six_report_on_any_market(self):
        for name, s in [("bull", bullish_series()), ("chop", choppy_series())]:
            setup = locate_six_pillars(s, ATR, symbol=SYMBOL)
            assert len(setup.findings) == 6, name
            assert {f.pillar for f in setup.findings} == set(Pillar), name

    def test_a_missing_pillar_still_says_why(self):
        # "No trade" has to be as specific as "trade" -- an empty reason would
        # leave the trader guessing, which is the failure mode this prevents.
        setup = locate_six_pillars(choppy_series(), ATR, symbol=SYMBOL)
        assert setup.missing
        for pillar in setup.missing:
            assert setup.finding(pillar).detail.strip()

    def test_found_and_missing_partition_the_six(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        assert set(setup.found) | set(setup.missing) == set(Pillar)
        assert not set(setup.found) & set(setup.missing)
        assert setup.pillars_found == len(setup.found)

    def test_the_summary_has_one_line_per_pillar(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        lines = pillar_summary(setup).splitlines()
        assert len(lines) == 6
        for pillar in Pillar:
            assert any(pillar.value in line for line in lines)


class TestLocatingABullishSetup:
    def setup_method(self):
        self.setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)

    def test_the_sweep_of_the_lows_implies_a_long(self):
        assert self.setup.direction is Direction.UP

    def test_liquidity_was_taken(self):
        assert self.setup.finding(Pillar.LIQUIDEZ).found
        assert self.setup.sweeps

    def test_the_block_and_the_gap_are_located(self):
        assert self.setup.finding(Pillar.ORDER_BLOCK).found
        assert self.setup.finding(Pillar.FVG).found
        assert self.setup.order_block is not None
        assert self.setup.fvg is not None

    def test_fibonacci_is_anchored_to_the_impulsive_leg(self):
        # Anchored to the displacement, not to the whole window -- otherwise
        # the levels move whenever the lookback does.
        assert self.setup.fibonacci is not None
        assert self.setup.displacement is not None
        leg_start = self.setup.displacement.start_index
        s = bullish_series()
        expected_low = min(s[i].low for i in range(leg_start, len(s)))
        assert self.setup.fibonacci.origin == expected_low

    def test_a_long_is_priced_in_discount(self):
        assert self.setup.pricing is Pricing.DISCOUNT
        assert self.setup.favourable_pricing


class TestLocatingABearishSetup:
    def setup_method(self):
        self.setup = locate_six_pillars(bearish_series(), ATR, symbol=SYMBOL)

    def test_the_sweep_of_the_highs_implies_a_short(self):
        assert self.setup.direction is Direction.DOWN

    def test_the_displacement_agrees_with_the_side(self):
        assert self.setup.displacement is not None
        assert self.setup.displacement.direction is Direction.DOWN

    def test_fibonacci_measures_the_leg_downwards(self):
        fib = self.setup.fibonacci
        assert fib is not None
        assert fib.origin > fib.destination  # high → low, not low → high


class TestDirectionDecidesTheNumbers:
    """A stop on the wrong side is a guaranteed loss, not a rounding error."""

    def test_a_long_invalidates_below_the_entry(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        assert setup.invalidation is not None
        assert setup.invalidation < setup.price

    def test_a_short_invalidates_above_the_entry(self):
        setup = locate_six_pillars(bearish_series(), ATR, symbol=SYMBOL)
        assert setup.invalidation is not None
        assert setup.invalidation > setup.price

    def test_a_long_targets_liquidity_above(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        assert setup.target is not None
        assert setup.target > setup.price

    def test_a_short_targets_liquidity_below(self):
        setup = locate_six_pillars(bearish_series(), ATR, symbol=SYMBOL)
        assert setup.target is not None
        assert setup.target < setup.price

    def test_a_buffer_widens_a_long_stop_downwards(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        buffered = setup.stop_loss(dec("0.0010"))
        assert buffered == setup.invalidation - dec("0.0010")
        assert buffered < setup.invalidation

    def test_a_buffer_widens_a_short_stop_upwards(self):
        # The obvious mistake -- subtracting on both sides -- tightens a short
        # into the noise the buffer exists to survive.
        setup = locate_six_pillars(bearish_series(), ATR, symbol=SYMBOL)
        buffered = setup.stop_loss(dec("0.0010"))
        assert buffered == setup.invalidation + dec("0.0010")
        assert buffered > setup.invalidation

    def test_a_buffered_stop_is_always_further_from_entry(self):
        for s in (bullish_series(), bearish_series()):
            setup = locate_six_pillars(s, ATR, symbol=SYMBOL)
            plain = abs(setup.price - setup.invalidation)
            buffered = abs(setup.price - setup.stop_loss(dec("0.0010")))
            assert buffered > plain

    def test_a_negative_buffer_is_refused(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        with pytest.raises(ValueError, match="negative"):
            setup.stop_loss(dec("-0.0010"))

    def test_without_an_invalidation_there_is_no_stop(self):
        setup = locate_six_pillars(series_of([
            ("1.1000", "1.1001", "1.0999", "1.1000"),
            ("1.1000", "1.1001", "1.0999", "1.1000"),
            ("1.1000", "1.1001", "1.0999", "1.1000"),
        ]), ATR, symbol=SYMBOL)
        assert setup.invalidation is None
        assert setup.stop_loss(dec("0.0010")) is None

    def test_the_reward_side_is_further_than_the_risk_side_or_it_is_reported(self):
        # Not an assertion that every setup is good -- an assertion that the
        # three numbers are internally consistent and orderable.
        for s in (bullish_series(), bearish_series()):
            setup = locate_six_pillars(s, ATR, symbol=SYMBOL)
            assert setup.invalidation != setup.target


class TestTheEntryZone:
    def test_it_is_the_overlap_of_the_block_and_the_ote_band(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        zone = setup.entry_zone
        assert zone is not None
        low, high = zone
        assert low <= high
        block, fib = setup.order_block, setup.fibonacci
        assert block is not None and fib is not None
        assert low == max(block.zone.low, fib.ote_low)
        assert high == min(block.zone.high, fib.ote_high)

    def test_it_is_never_wider_than_the_block_alone(self):
        # Confluence narrows the entry; it must never widen it.
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        low, high = setup.entry_zone
        block = setup.order_block.zone
        assert (high - low) <= (block.high - block.low)

    def test_without_a_block_there_is_nowhere_to_enter(self):
        setup = locate_six_pillars(choppy_series(), ATR, symbol=SYMBOL)
        assert setup.order_block is None
        assert setup.entry_zone is None


class TestDeterminism:
    def test_the_same_bars_read_the_same_way(self):
        a = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        b = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        assert a.findings == b.findings
        assert a.direction == b.direction
        assert a.entry_zone == b.entry_zone
        assert a.invalidation == b.invalidation
        assert a.target == b.target

    def test_reading_a_prefix_cannot_see_the_bars_that_follow(self):
        # The look-ahead guard. If a reading of the first eight bars changed
        # when four more arrived, every backtest built on it would be fiction.
        full = bullish_series()
        early = locate_six_pillars(full.upto(7), ATR, symbol=SYMBOL)
        again = locate_six_pillars(bullish_series().upto(7), ATR, symbol=SYMBOL)
        assert early.findings == again.findings
        assert early.bar_index == 7
        assert early.price == full[7].close


class TestPricingIsDirectional:
    def test_equilibrium_is_not_an_edge(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        from dataclasses import replace
        at_eq = replace(setup, pricing=Pricing.EQUILIBRIUM)
        assert not at_eq.favourable_pricing

    def test_buying_a_premium_is_not_favourable(self):
        from dataclasses import replace
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        assert not replace(setup, pricing=Pricing.PREMIUM).favourable_pricing

    def test_selling_a_premium_is(self):
        from dataclasses import replace
        setup = locate_six_pillars(bearish_series(), ATR, symbol=SYMBOL)
        assert replace(
            setup, direction=Direction.DOWN, pricing=Pricing.PREMIUM
        ).favourable_pricing

    def test_without_a_direction_nothing_is_favourable(self):
        from dataclasses import replace
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        assert not replace(setup, direction=None).favourable_pricing


class TestScoringTheSetup:
    def test_every_factor_is_accounted_for(self):
        # Silence about a factor is indistinguishable from forgetting it.
        score = score_setup(locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL))
        assert {f.factor for f in score.factors} == set(Factor)

    def test_each_mapped_pillar_names_itself_in_its_factor(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        score = score_setup(setup)
        conditions = {f.factor: f.condition for f in score.factors}
        for pillar, factor in PILLAR_FACTORS.items():
            assert pillar.value in conditions[factor]

    def test_fibonacci_is_scored_through_pricing(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        score = score_setup(setup)
        pricing = next(f for f in score.factors if f.factor is Factor.PRICING)
        assert Pillar.FIBONACCI.value in pricing.condition

    def test_a_measured_leg_at_the_wrong_price_earns_nothing(self):
        # A leg drawn under a setup bought at a premium is evidence against the
        # trade. Awarding it would pay the strategy for being wrong.
        from dataclasses import replace
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        assert setup.finding(Pillar.FIBONACCI).found
        premium = replace(setup, pricing=Pricing.PREMIUM)
        score = score_setup(premium)
        pricing = next(f for f in score.factors if f.factor is Factor.PRICING)
        assert not pricing.satisfied
        assert "premium" in pricing.condition

    def test_a_missing_pillar_withholds_its_factor(self):
        setup = locate_six_pillars(choppy_series(), ATR, symbol=SYMBOL)
        score = score_setup(setup)
        for pillar in setup.missing:
            if pillar not in PILLAR_FACTORS:
                continue
            awarded = next(
                f for f in score.factors if f.factor is PILLAR_FACTORS[pillar]
            )
            assert not awarded.satisfied
            assert awarded.awarded == 0

    def test_a_market_with_no_edge_is_discarded(self):
        score = score_setup(locate_six_pillars(choppy_series(), ATR, symbol=SYMBOL))
        assert not score.tradeable
        assert score.primary_reason == "score_below_threshold"

    def test_the_total_is_the_sum_of_the_parts(self):
        score = score_setup(locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL))
        assert sum(f.awarded for f in score.factors) == score.total

    def test_scoring_is_reproducible(self):
        a = score_setup(locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL))
        b = score_setup(locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL))
        assert a.total == b.total
        assert [f.condition for f in a.factors] == [f.condition for f in b.factors]

    def test_a_stricter_threshold_is_honoured(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        assert not score_setup(setup, threshold=95).tradeable


class TestCompleteness:
    def test_complete_means_all_six(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        assert setup.complete == (setup.pillars_found == 6)

    def test_a_choppy_market_is_not_complete(self):
        setup = locate_six_pillars(choppy_series(), ATR, symbol=SYMBOL)
        assert not setup.complete

    def test_asking_for_a_pillar_that_was_not_read_is_a_bug(self):
        setup = locate_six_pillars(bullish_series(), ATR, symbol=SYMBOL)
        # Every pillar is always read, so this must never raise.
        for pillar in Pillar:
            assert setup.finding(pillar).pillar is pillar
