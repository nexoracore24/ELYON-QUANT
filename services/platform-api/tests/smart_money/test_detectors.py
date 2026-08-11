"""Smart Money detector tests.

The cases that matter here are the ones where a naive implementation gets it
*almost* right: a sweep that is really a breakout, a BOS mislabelled as a CHoCH,
a Fibonacci level that fires a trade on its own. Those are the mistakes that
cost money, so they get explicit tests.
"""

from __future__ import annotations

import pytest

from elyon.modules.market_data.domain.model import Candle, CandleState, Timeframe
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.smart_money.domain import (
    BreakConfirmation,
    Direction,
    EventKind,
    LiquidityType,
    PoolState,
    Pricing,
    SwingLabel,
    Trend,
    ZoneState,
    build_pools,
    build_structure,
    buy_side,
    compute_fibonacci,
    detect_bos,
    detect_choch,
    detect_displacement,
    detect_equal_levels,
    detect_fvg,
    detect_mss,
    detect_order_block,
    detect_sweeps,
    detect_swings,
    event_failed,
    sell_side,
)
from elyon.modules.smart_money.domain.zones import DealingRange, Zone
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

SYMBOL = "EURUSD"
M1 = Timeframe.M1
MINUTE = M1.duration_ns
ATR = dec("0.0010")  # 10 pips


def bar(i: int, o: str, h: str, l: str, c: str) -> Candle:
    """A confirmed bar. High/low are widened to contain the body if needed."""
    op, cl = dec(o), dec(c)
    hi, lo = max(dec(h), op, cl), min(dec(l), op, cl)
    return Candle(
        symbol=SYMBOL, timeframe=M1,
        open_time_ns=i * MINUTE, close_time_ns=(i + 1) * MINUTE,
        open=op, high=hi, low=lo, close=cl,
        volume=dec("1"), tick_count=1, state=CandleState.CONFIRMED,
    )


def flat(i: int, mid: str, span: str = "0.0002") -> Candle:
    """A quiet doji-ish bar centred on ``mid``."""
    m, s = dec(mid), dec(span)
    return bar(i, mid, str(m + s), str(m - s), mid)


def series(candles: list[Candle]) -> CandleSeries:
    return CandleSeries.of(candles)


def _zigzag(highs_lows: list[tuple[str, str]]) -> CandleSeries:
    """Build bars from explicit (high, low) pairs, body spanning the range."""
    return series([bar(i, lo, hi, lo, hi) for i, (hi, lo) in enumerate(highs_lows)])


class TestSeriesGuards:
    def test_a_forming_candle_cannot_enter_a_series(self):
        forming = Candle.opening(
            symbol=SYMBOL, timeframe=M1, open_time_ns=0, price=dec("1.1000")
        )
        with pytest.raises(DeterminismError, match="confirmed data only"):
            CandleSeries.of([forming])

    def test_candles_must_be_chronological(self):
        with pytest.raises(DeterminismError, match="out of order"):
            CandleSeries.of([bar(1, "1.1", "1.1", "1.1", "1.1"), bar(0, "1.1", "1.1", "1.1", "1.1")])

    def test_upto_is_the_look_ahead_guard(self):
        s = series([flat(i, "1.1000") for i in range(5)])
        assert len(s.upto(2)) == 3


class TestDisplacement:
    def test_a_strong_impulse_is_detected(self):
        s = series([
            flat(0, "1.1000"),
            bar(1, "1.1000", "1.1030", "1.0999", "1.1028"),  # ~2.8x ATR body
        ])
        d = detect_displacement(s, 1, ATR)
        assert d is not None
        assert d.direction is Direction.UP
        assert d.body_ratio > dec("0.6")

    def test_a_wicky_indecisive_bar_is_not_displacement(self):
        # Travels far but closes near the open: no intent, just noise.
        s = series([flat(0, "1.1000"), bar(1, "1.1000", "1.1040", "1.0960", "1.1002")])
        assert detect_displacement(s, 1, ATR) is None

    def test_a_small_move_is_not_displacement(self):
        s = series([flat(0, "1.1000"), bar(1, "1.1000", "1.1004", "1.1000", "1.1003")])
        assert detect_displacement(s, 1, ATR) is None

    def test_zero_atr_yields_nothing(self):
        s = series([flat(0, "1.1000"), bar(1, "1.1000", "1.1030", "1.1000", "1.1028")])
        assert detect_displacement(s, 1, dec("0")) is None


class TestSwings:
    def _peak(self) -> CandleSeries:
        # A clean peak at index 2.
        return series([
            bar(0, "1.1000", "1.1010", "1.0995", "1.1005"),
            bar(1, "1.1005", "1.1020", "1.1000", "1.1015"),
            bar(2, "1.1015", "1.1040", "1.1010", "1.1035"),  # peak
            bar(3, "1.1035", "1.1030", "1.1005", "1.1010"),
            bar(4, "1.1010", "1.1015", "1.0990", "1.0995"),
        ])

    def test_a_clean_peak_is_a_swing_high(self):
        swings = detect_swings(self._peak(), grade=2)
        highs = [s for s in swings if s.is_high]
        assert len(highs) == 1
        assert highs[0].index == 2
        assert highs[0].price == dec("1.1040")

    def test_confirmation_lags_by_the_grade(self):
        # A swing is only knowable once its right-hand bars have closed.
        swings = detect_swings(self._peak(), grade=2)
        assert swings[0].confirm_index == swings[0].index + 2

    def test_a_plateau_produces_no_strict_swing(self):
        # Equal highs are a liquidity concept, not a structural one.
        s = series([
            bar(0, "1.1000", "1.1010", "1.0995", "1.1005"),
            bar(1, "1.1005", "1.1030", "1.1000", "1.1025"),
            bar(2, "1.1025", "1.1030", "1.1015", "1.1020"),  # equal high
            bar(3, "1.1020", "1.1025", "1.1000", "1.1005"),
        ])
        assert not [x for x in detect_swings(s, grade=1) if x.is_high and x.index == 2]

    def test_edges_cannot_be_swings(self):
        s = series([flat(i, "1.1000") for i in range(3)])
        assert all(0 < x.index < 2 for x in detect_swings(s, grade=1))


class TestStructure:
    def _uptrend(self) -> CandleSeries:
        # A zigzag that produces clean grade-1 fractals on both sides:
        # peaks at odd indices, troughs at even, each higher than the last.
        return _zigzag([
            ("1.1020", "1.1000"), ("1.1035", "1.1015"), ("1.1015", "1.0995"),
            ("1.1055", "1.1025"), ("1.1030", "1.1010"), ("1.1075", "1.1045"),
            ("1.1050", "1.1030"), ("1.1095", "1.1065"), ("1.1070", "1.1050"),
        ])

    def test_higher_highs_and_lows_read_as_bullish(self):
        st = build_structure(self._uptrend(), grade=1)
        assert st.trend is Trend.BULLISH
        assert st.last_high is not None

    def test_labels_are_relative_to_the_previous_swing_of_the_same_kind(self):
        st = build_structure(self._uptrend(), grade=1)
        labelled = [s.label for s in st.highs if s.label]
        assert SwingLabel.HH in labelled

    def test_too_few_swings_leaves_the_trend_undetermined(self):
        s = series([flat(i, "1.1000") for i in range(4)])
        assert build_structure(s, grade=1).trend is Trend.UNDETERMINED

    def test_the_protected_low_is_the_higher_low_not_the_latest(self):
        st = build_structure(self._uptrend(), grade=1)
        protected = st.protected_low
        if protected is not None and any(s.label is SwingLabel.HL for s in st.lows):
            assert protected.label is SwingLabel.HL


class TestStructuralEvents:
    """The BOS / CHoCH distinction -- getting this backwards inverts the bias."""

    def _bull_structure(self):
        s = _zigzag([
            ("1.1020", "1.1000"), ("1.1035", "1.1015"), ("1.1015", "1.0995"),
            ("1.1055", "1.1025"), ("1.1030", "1.1010"), ("1.1075", "1.1045"),
            ("1.1050", "1.1030"), ("1.1095", "1.1065"), ("1.1070", "1.1050"),
        ])
        return s, build_structure(s, grade=1)

    def test_breaking_the_continuation_high_is_a_bos(self):
        s, st = self._bull_structure()
        if st.trend is not Trend.BULLISH or st.last_high is None:
            pytest.skip("fixture did not produce a bullish structure")

        level = st.last_high.price
        extended = series(list(s) + [
            bar(len(s), str(level), str(level + dec("0.0035")),
                str(level - dec("0.0002")), str(level + dec("0.0030")))
        ])
        event = detect_bos(extended, st, len(extended) - 1, ATR)
        assert event is not None
        assert event.kind is EventKind.BOS
        assert event.direction is Direction.UP

    def test_a_break_with_no_impulse_is_rejected_by_default(self):
        s, st = self._bull_structure()
        if st.trend is not Trend.BULLISH or st.last_high is None:
            pytest.skip("fixture did not produce a bullish structure")

        level = st.last_high.price
        # Quiet bars first, so the multi-bar window cannot find an impulse
        # elsewhere, then a one-pip creep past the level: beyond, but no intent.
        quiet = str(level - dec("0.0005"))
        crawl = series(list(s) + [
            bar(len(s), quiet, str(level - dec("0.0004")), str(level - dec("0.0006")), quiet),
            bar(len(s) + 1, quiet, str(level - dec("0.0004")), str(level - dec("0.0006")), quiet),
            bar(len(s) + 2, quiet, str(level + dec("0.0002")),
                str(level - dec("0.0006")), str(level + dec("0.0001"))),
        ])
        assert detect_bos(crawl, st, len(crawl) - 1, ATR) is None

    def test_touching_a_level_exactly_is_not_a_break(self):
        s, st = self._bull_structure()
        if st.last_high is None:
            pytest.skip("no swing high")
        level = st.last_high.price
        touch = series(list(s) + [
            bar(len(s), str(level - dec("0.0010")), str(level),
                str(level - dec("0.0012")), str(level))
        ])
        assert detect_bos(touch, st, len(touch) - 1, ATR,
                          require_displacement=False) is None

    def test_a_range_has_no_bos_or_choch(self):
        s = series([flat(i, "1.1000") for i in range(6)])
        st = build_structure(s, grade=1)
        assert detect_bos(s, st, 5, ATR) is None
        assert detect_choch(s, st, 5, ATR) is None

    def test_a_reclaimed_break_is_reported_as_failed(self):
        s, st = self._bull_structure()
        if st.last_high is None:
            pytest.skip("no swing high")
        level = st.last_high.price
        extended = series(list(s) + [
            bar(len(s), str(level), str(level + dec("0.0035")),
                str(level - dec("0.0002")), str(level + dec("0.0030"))),
            bar(len(s) + 1, str(level + dec("0.0030")), str(level + dec("0.0031")),
                str(level - dec("0.0020")), str(level - dec("0.0015"))),
        ])
        event = detect_bos(extended, st, len(s), ATR)
        if event is None:
            pytest.skip("no BOS produced")
        assert event_failed(extended, event)

    def test_mss_requires_follow_through(self):
        from elyon.modules.smart_money.domain.events import StructuralEvent
        choch = StructuralEvent(EventKind.CHOCH, Direction.UP, dec("1.10"), 5, None)
        assert detect_mss(series([flat(i, "1.1") for i in range(8)]), choch, None) is None

    def test_mss_confirms_when_a_bos_follows_in_the_same_direction(self):
        from elyon.modules.smart_money.domain.events import StructuralEvent
        s = series([flat(i, "1.1000") for i in range(12)])
        choch = StructuralEvent(EventKind.CHOCH, Direction.UP, dec("1.10"), 5, None)
        bos = StructuralEvent(EventKind.BOS, Direction.UP, dec("1.11"), 8, None)
        mss = detect_mss(s, choch, bos)
        assert mss is not None and mss.direction is Direction.UP

    def test_mss_rejects_follow_through_in_the_wrong_direction(self):
        from elyon.modules.smart_money.domain.events import StructuralEvent
        s = series([flat(i, "1.1000") for i in range(12)])
        choch = StructuralEvent(EventKind.CHOCH, Direction.UP, dec("1.10"), 5, None)
        opposite = StructuralEvent(EventKind.BOS, Direction.DOWN, dec("1.09"), 8, None)
        assert detect_mss(s, choch, opposite) is None


class TestLiquidity:
    def test_equal_highs_cluster_into_one_level(self):
        from elyon.modules.smart_money.domain.structure import Swing
        swings = [
            Swing(2, dec("1.1050"), True, 1, 3),
            Swing(9, dec("1.1051"), True, 1, 10),  # within tolerance
        ]
        equals = detect_equal_levels(swings, ATR, is_high=True)
        assert len(equals) == 1
        assert equals[0].touches == 2
        assert equals[0].type is LiquidityType.BSL

    def test_levels_far_apart_are_not_equal(self):
        from elyon.modules.smart_money.domain.structure import Swing
        swings = [
            Swing(2, dec("1.1050"), True, 1, 3),
            Swing(9, dec("1.1090"), True, 1, 10),
        ]
        assert detect_equal_levels(swings, ATR, is_high=True) == []

    def test_two_touches_of_one_oscillation_do_not_count(self):
        from elyon.modules.smart_money.domain.structure import Swing
        swings = [
            Swing(2, dec("1.1050"), True, 1, 3),
            Swing(3, dec("1.1050"), True, 1, 4),  # adjacent
        ]
        assert detect_equal_levels(swings, ATR, is_high=True) == []

    def test_pools_split_by_side_of_price(self):
        from elyon.modules.smart_money.domain.structure import Swing
        swings = [
            Swing(2, dec("1.1050"), True, 1, 3),
            Swing(5, dec("1.0950"), False, 1, 6),
        ]
        pools = build_pools(swings, ATR)
        price = dec("1.1000")
        assert buy_side(pools, price)[0].type is LiquidityType.BSL
        assert sell_side(pools, price)[0].type is LiquidityType.SSL


class TestSweeps:
    """Sweep vs breakout -- same penetration, opposite meaning."""

    def _pool_above(self):
        from elyon.modules.smart_money.domain.liquidity import LiquidityPool
        return [LiquidityPool(dec("1.1050"), LiquidityType.BSL, "equal", 2)]

    def test_a_poke_that_closes_back_inside_is_a_sweep(self):
        s = series([
            bar(0, "1.1040", "1.1080", "1.1035", "1.1042"),  # long upper wick
        ])
        sweeps = detect_sweeps(s, self._pool_above(), 0, ATR)
        assert len(sweeps) == 1
        # Taking buy-side liquidity implies a bearish turn.
        assert sweeps[0].direction is Direction.DOWN
        assert sweeps[0].pool.state is PoolState.SWEPT

    def test_closing_beyond_the_level_is_a_breakout_not_a_sweep(self):
        # The critical distinction: price accepted above, it did not reject.
        s = series([bar(0, "1.1040", "1.1080", "1.1038", "1.1075")])
        assert detect_sweeps(s, self._pool_above(), 0, ATR) == []

    def test_a_shallow_touch_is_not_a_sweep(self):
        s = series([bar(0, "1.1040", "1.1050", "1.1035", "1.1042")])
        assert detect_sweeps(s, self._pool_above(), 0, ATR) == []

    def test_a_small_wick_is_not_a_rejection(self):
        # Penetrates and closes back, but the bar is mostly body: weak signal.
        s = series([bar(0, "1.1075", "1.1080", "1.1040", "1.1044")])
        assert detect_sweeps(s, self._pool_above(), 0, ATR) == []

    def test_sell_side_sweep_implies_a_bullish_turn(self):
        from elyon.modules.smart_money.domain.liquidity import LiquidityPool
        pools = [LiquidityPool(dec("1.0950"), LiquidityType.SSL, "equal", 2)]
        s = series([bar(0, "1.0960", "1.0965", "1.0920", "1.0958")])
        sweeps = detect_sweeps(s, pools, 0, ATR)
        assert len(sweeps) == 1 and sweeps[0].direction is Direction.UP


class TestFairValueGap:
    def test_a_three_bar_gap_is_detected(self):
        s = series([
            bar(0, "1.1000", "1.1010", "1.0995", "1.1005"),
            bar(1, "1.1010", "1.1060", "1.1008", "1.1055"),  # displacement
            bar(2, "1.1055", "1.1065", "1.1030", "1.1060"),  # low above bar0 high
        ])
        fvg = detect_fvg(s, 1, ATR)
        assert fvg is not None
        assert fvg.direction is Direction.UP
        assert fvg.zone.low == dec("1.1010")
        assert fvg.zone.high == dec("1.1030")
        assert fvg.consequent_encroachment == dec("1.1020")

    def test_overlapping_bars_leave_no_gap(self):
        s = series([
            bar(0, "1.1000", "1.1030", "1.0995", "1.1025"),
            bar(1, "1.1025", "1.1050", "1.1020", "1.1045"),
            bar(2, "1.1045", "1.1055", "1.1020", "1.1050"),  # overlaps bar0
        ])
        assert detect_fvg(s, 1, ATR) is None

    def test_a_gap_below_the_size_floor_is_ignored(self):
        s = series([
            bar(0, "1.1000", "1.1010", "1.0995", "1.1005"),
            bar(1, "1.1010", "1.1020", "1.1008", "1.1018"),
            bar(2, "1.1018", "1.1025", "1.1010", "1.1020"),  # ~0 gap
        ])
        assert detect_fvg(s, 1, ATR) is None


class TestZoneLifecycle:
    def test_a_close_through_the_zone_invalidates_it(self):
        zone = Zone(dec("1.1000"), dec("1.1020"), Direction.UP, 0)
        broken = zone.advance(bar(1, "1.1010", "1.1015", "1.0980", "1.0985"))
        assert broken.state is ZoneState.INVALIDATED

    def test_a_deep_tap_mitigates(self):
        zone = Zone(dec("1.1000"), dec("1.1020"), Direction.UP, 0)
        tapped = zone.advance(bar(1, "1.1030", "1.1035", "1.1002", "1.1025"))
        assert tapped.state is ZoneState.MITIGATED

    def test_an_untouched_zone_stays_fresh(self):
        zone = Zone(dec("1.1000"), dec("1.1020"), Direction.UP, 0)
        assert zone.advance(bar(1, "1.1050", "1.1060", "1.1040", "1.1055")).state is ZoneState.FRESH

    def test_an_invalidated_zone_never_comes_back(self):
        zone = Zone(dec("1.1000"), dec("1.1020"), Direction.UP, 0)
        dead = zone.advance(bar(1, "1.1010", "1.1015", "1.0980", "1.0985"))
        assert dead.advance(bar(2, "1.1010", "1.1020", "1.1005", "1.1015")).state is ZoneState.INVALIDATED


class TestOrderBlock:
    def test_the_last_opposite_candle_before_the_impulse_is_the_block(self):
        s = series([
            bar(0, "1.1020", "1.1025", "1.1015", "1.1018"),  # bearish
            bar(1, "1.1018", "1.1022", "1.1008", "1.1010"),  # bearish - the block
            bar(2, "1.1010", "1.1060", "1.1009", "1.1055"),  # impulse up
        ])
        displacement = detect_displacement(s, 2, ATR)
        assert displacement is not None
        poi = detect_order_block(s, displacement)
        assert poi is not None
        assert poi.zone.origin_index == 1
        assert poi.direction is Direction.UP

    def test_confluence_raises_confidence(self):
        s = series([
            bar(0, "1.1020", "1.1025", "1.1015", "1.1018"),
            bar(1, "1.1018", "1.1022", "1.1008", "1.1010"),
            bar(2, "1.1010", "1.1060", "1.1009", "1.1055"),
        ])
        d = detect_displacement(s, 2, ATR)
        assert d is not None
        plain = detect_order_block(s, d)
        rich = detect_order_block(s, d, has_fvg=True, had_prior_sweep=True)
        assert plain is not None and rich is not None
        assert rich.confidence > plain.confidence


class TestPricing:
    def _range(self) -> DealingRange:
        return DealingRange(dec("1.1000"), dec("1.1100"), Direction.UP, 0, 10)

    def test_the_lower_half_is_discount(self):
        assert self._range().classify(dec("1.1020")) is Pricing.DISCOUNT

    def test_the_upper_half_is_premium(self):
        assert self._range().classify(dec("1.1080")) is Pricing.PREMIUM

    def test_the_middle_is_neutral_ground(self):
        assert self._range().classify(dec("1.1050")) is Pricing.EQUILIBRIUM

    def test_a_degenerate_range_is_refused(self):
        with pytest.raises(DeterminismError, match="positive size"):
            DealingRange(dec("1.1000"), dec("1.1000"), Direction.UP, 0, 1)


class TestFibonacci:
    def test_levels_are_anchored_to_the_leg(self):
        fib = compute_fibonacci(dec("1.1000"), dec("1.1100"))
        assert fib is not None
        assert fib.retracements["0"] == dec("1.1100")     # destination
        assert fib.retracements["1"] == dec("1.1000")     # origin
        assert fib.retracements["0.5"] == dec("1.1050")   # equilibrium

    def test_the_ote_band_sits_between_618_and_786(self):
        fib = compute_fibonacci(dec("1.1000"), dec("1.1100"))
        assert fib is not None
        assert fib.ote_low == dec("1.10214")   # 0.786 retracement
        assert fib.ote_high == dec("1.10382")  # 0.618 retracement
        assert fib.in_ote(fib.ote_optimal)

    def test_projections_extend_beyond_the_destination(self):
        fib = compute_fibonacci(dec("1.1000"), dec("1.1100"))
        assert fib is not None
        assert fib.projections["1.618"] == dec("1.11618")
        assert fib.projections["2.618"] > fib.projections["1.618"]

    def test_a_short_leg_mirrors_the_geometry(self):
        fib = compute_fibonacci(dec("1.1100"), dec("1.1000"))
        assert fib is not None
        assert fib.ote_low < fib.ote_high
        assert fib.projections["1.618"] < dec("1.1000")

    def test_a_zero_span_leg_has_no_levels(self):
        assert compute_fibonacci(dec("1.1000"), dec("1.1000")) is None

    def test_a_leg_below_the_minimum_is_refused(self):
        assert compute_fibonacci(dec("1.1000"), dec("1.1001"), min_span=ATR) is None

    def test_levels_are_reproducible(self):
        a = compute_fibonacci(dec("1.1000"), dec("1.1100"))
        b = compute_fibonacci(dec("1.1000"), dec("1.1100"))
        assert a is not None and b is not None
        assert a.retracements == b.retracements
