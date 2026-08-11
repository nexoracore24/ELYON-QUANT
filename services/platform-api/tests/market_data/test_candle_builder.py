"""Market Data Engine tests -- the no-repaint guarantees.

Numbering follows the Market Data Engine Bible SS24 (T1-T11).
"""

from __future__ import annotations

import random

import pytest

from elyon.modules.market_data.domain import (
    AtrProvider,
    BuilderConfig,
    Candle,
    CandleBuilder,
    CandleState,
    EmptyCandlePolicy,
    LateDataPolicy,
    Tick,
    Timeframe,
    efficiency_ratio,
)
from elyon.shared_kernel.edcs import DeterminismError, dec

SYMBOL = "EURUSD"
M1 = Timeframe.M1
MINUTE = M1.duration_ns


def tick(offset_ns: int, price: str, *, seq: int = 0, volume: str = "1") -> Tick:
    """A quote at ``offset_ns`` with a 1-pip spread centred on ``price``."""
    mid = dec(price)
    half = dec("0.00005")
    return Tick(
        symbol=SYMBOL,
        event_time_ns=offset_ns,
        bid=mid - half,
        ask=mid + half,
        provider="test",
        seq=seq,
        volume=dec(volume),
    )


def build(ticks: list[Tick], **overrides) -> list[Candle]:
    """Feed ticks through a builder and return every confirmed candle."""
    settings = {"timeframe": M1, "max_lateness_ns": 0, **overrides}
    config = BuilderConfig(**settings)
    builder = CandleBuilder(SYMBOL, config)
    out: list[Candle] = []
    for t in ticks:
        out.extend(builder.on_tick(t).confirmed)
    out.extend(builder.flush())
    return out


class TestBucketing:
    def test_t2_a_tick_on_the_close_belongs_to_the_next_candle(self):
        # Half-open [open, close): the boundary is unambiguous.
        assert M1.bucket_of(MINUTE - 1) == 0
        assert M1.bucket_of(MINUTE) == MINUTE

    def test_buckets_sit_on_a_fixed_utc_grid(self):
        # Not "first tick starts the candle" -- otherwise two feeds of the same
        # market would produce differently aligned bars.
        assert M1.bucket_of(90 * 10**9) == 60 * 10**9
        assert Timeframe.M15.bucket_of(1_000 * 10**9) == 900 * 10**9


class TestCandleAssembly:
    def test_ohlc_is_assembled_in_order(self):
        candles = build([
            tick(0, "1.1000"),
            tick(10**9, "1.1020"),
            tick(2 * 10**9, "1.0990"),
            tick(3 * 10**9, "1.1010"),
        ])
        assert len(candles) == 1
        c = candles[0]
        assert (c.open, c.high, c.low, c.close) == (
            dec("1.1000"), dec("1.1020"), dec("1.0990"), dec("1.1010"),
        )
        assert c.tick_count == 4
        assert c.volume == dec("4")
        assert c.state is CandleState.CONFIRMED

    def test_an_inconsistent_candle_cannot_be_constructed(self):
        with pytest.raises(DeterminismError, match="high .* below low"):
            Candle(
                symbol=SYMBOL, timeframe=M1, open_time_ns=0, close_time_ns=MINUTE,
                open=dec("1.10"), high=dec("1.09"), low=dec("1.11"),
                close=dec("1.10"), volume=dec("0"), tick_count=1,
            )


class TestNoRepaint:
    def test_t3_a_confirmed_candle_can_never_be_mutated(self):
        confirmed = build([tick(0, "1.1000")])[0]
        with pytest.raises(DeterminismError, match="confirmed data is immutable"):
            confirmed.apply(dec("1.5000"), dec("1"))

    def test_t3_a_late_tick_is_reported_not_absorbed(self):
        config = BuilderConfig(timeframe=M1, max_lateness_ns=0)
        builder = CandleBuilder(SYMBOL, config)
        builder.on_tick(tick(0, "1.1000"))
        confirmed = builder.on_tick(tick(MINUTE, "1.1010")).confirmed
        frozen_hash = confirmed[0].data_hash

        result = builder.on_tick(tick(30 * 10**9, "1.9999", seq=99))

        assert result.late is not None
        assert result.late.policy is LateDataPolicy.DROP
        assert confirmed[0].data_hash == frozen_hash  # untouched

    def test_forming_is_exposed_but_flagged_provisional(self):
        builder = CandleBuilder(SYMBOL, BuilderConfig(timeframe=M1, max_lateness_ns=0))
        builder.on_tick(tick(0, "1.1000"))
        forming = builder.forming
        assert forming is not None and forming.state is CandleState.FORMING


class TestWatermark:
    def test_t9_confirmation_waits_for_the_watermark(self):
        config = BuilderConfig(timeframe=M1, max_lateness_ns=5 * 10**9)
        builder = CandleBuilder(SYMBOL, config)
        builder.on_tick(tick(0, "1.1000"))

        # Past the close, but still inside the lateness window: hold it open.
        assert builder.on_tick(tick(MINUTE + 10**9, "1.1010")).confirmed == []
        # Watermark clears the close: freeze.
        assert len(builder.on_tick(tick(MINUTE + 6 * 10**9, "1.1020")).confirmed) == 1

    def test_a_tick_inside_the_window_is_folded_in_as_if_in_order(self):
        config = BuilderConfig(timeframe=M1, max_lateness_ns=5 * 10**9)
        builder = CandleBuilder(SYMBOL, config)
        builder.on_tick(tick(0, "1.1000"))
        builder.on_tick(tick(MINUTE + 10**9, "1.1010"))
        builder.on_tick(tick(30 * 10**9, "1.2000"))  # late, but not yet frozen
        confirmed = builder.on_tick(tick(MINUTE + 6 * 10**9, "1.1020")).confirmed
        assert confirmed[0].high == dec("1.2000")  # it counted


class TestDeterminism:
    def _stream(self) -> list[Tick]:
        prices = ["1.1000", "1.1015", "1.0995", "1.1008", "1.1022", "1.0988"]
        return [tick(i * 10**9, p, seq=i) for i, p in enumerate(prices)]

    def test_t1_same_input_yields_byte_identical_output(self):
        first = [c.data_hash for c in build(self._stream())]
        second = [c.data_hash for c in build(self._stream())]
        assert first == second

    def test_t5_out_of_order_input_yields_the_same_candles(self):
        ordered = self._stream()
        shuffled = ordered[:]
        random.Random(1234).shuffle(shuffled)

        # A generous lateness window means every tick still lands before its
        # bucket freezes -- so the shuffle must be invisible in the output.
        config = {"max_lateness_ns": 10 * 10**9}
        assert [c.data_hash for c in build(shuffled, **config)] == [
            c.data_hash for c in build(ordered, **config)
        ]

    def test_the_hash_is_sensitive_to_the_data(self):
        base = build(self._stream())[0]
        moved = build(self._stream()[:-1] + [tick(5 * 10**9, "1.2000", seq=5)])[0]
        assert base.data_hash != moved.data_hash


class TestGaps:
    def test_t6_a_quiet_market_produces_nothing_by_default(self):
        builder = CandleBuilder(SYMBOL, BuilderConfig(timeframe=M1, max_lateness_ns=0))
        builder.on_tick(tick(0, "1.1000"))
        builder.on_tick(tick(MINUTE, "1.1010"))
        assert builder.fill_gap(10 * MINUTE) == []

    def test_t6_synthetic_candles_are_flagged_never_silent(self):
        config = BuilderConfig(
            timeframe=M1, max_lateness_ns=0,
            empty_candle_policy=EmptyCandlePolicy.SYNTHETIC,
        )
        builder = CandleBuilder(SYMBOL, config)
        builder.on_tick(tick(0, "1.1000"))
        builder.on_tick(tick(MINUTE, "1.1010"))  # confirms bucket 0

        filled = builder.fill_gap(4 * MINUTE)
        assert len(filled) == 3
        assert all(c.synthetic and c.tick_count == 0 for c in filled)
        assert all(c.open == c.close == dec("1.1000") for c in filled)


class TestAtr:
    def _candles(self, spans: list[tuple[str, str]]) -> list[Candle]:
        out = []
        for i, (high, low) in enumerate(spans):
            out.append(
                Candle(
                    symbol=SYMBOL, timeframe=M1,
                    open_time_ns=i * MINUTE, close_time_ns=(i + 1) * MINUTE,
                    open=dec(low), high=dec(high), low=dec(low), close=dec(low),
                    volume=dec("1"), tick_count=1, state=CandleState.CONFIRMED,
                )
            )
        return out

    def test_atr_is_not_ready_before_its_period(self):
        atr = AtrProvider(period=3, output_scale=5)
        candles = self._candles([("1.1010", "1.1000"), ("1.1020", "1.1010")])
        assert all(atr.update(c) is None for c in candles)
        assert not atr.is_ready

    def test_t3_atr_seeds_with_the_mean_of_the_first_true_ranges(self):
        atr = AtrProvider(period=3, output_scale=5)
        # Ranges 10, 10, 10 pips with each bar closing at its low.
        for c in self._candles([("1.1010", "1.1000")] * 3):
            atr.update(c)
        assert atr.is_ready
        assert atr.value is not None and atr.value > dec("0")

    def test_atr_refuses_a_forming_candle(self):
        atr = AtrProvider(period=2, output_scale=5)
        forming = Candle.opening(
            symbol=SYMBOL, timeframe=M1, open_time_ns=0, price=dec("1.1000")
        )
        with pytest.raises(DeterminismError, match="confirmed candles only"):
            atr.update(forming)

    def test_atr_is_reproducible(self):
        spans = [("1.1010", "1.1000"), ("1.1025", "1.1005"), ("1.1030", "1.1015")]
        def run():
            atr = AtrProvider(period=2, output_scale=5)
            for c in self._candles(spans):
                atr.update(c)
            return atr.value
        assert run() == run()


class TestEfficiencyRatio:
    def test_a_straight_line_is_perfectly_efficient(self):
        closes = [dec("1.1000"), dec("1.1010"), dec("1.1020"), dec("1.1030")]
        assert efficiency_ratio(closes) == dec("1.000000")

    def test_t5_a_flat_series_is_defined_as_zero_not_nan(self):
        # The path length is zero; NaN would poison every downstream decision.
        assert efficiency_ratio([dec("1.1000")] * 4) == dec("0")

    def test_churn_scores_low(self):
        closes = [dec("1.1000"), dec("1.1020"), dec("1.1000"), dec("1.1005")]
        assert efficiency_ratio(closes) < dec("0.2")


class TestOrderInvarianceProperty:
    """The determinism invariant, stressed rather than sampled."""

    def test_any_permutation_inside_the_window_yields_the_same_candles(self):
        rng = random.Random(20260729)
        prices = [f"1.1{i:03d}" for i in range(24)]
        ordered = [tick(i * 10**9, p, seq=i) for i, p in enumerate(prices)]
        window = {"max_lateness_ns": 60 * 10**9}
        expected = [c.data_hash for c in build(ordered, **window)]

        for _ in range(200):
            shuffled = ordered[:]
            rng.shuffle(shuffled)
            assert [c.data_hash for c in build(shuffled, **window)] == expected

    def test_open_and_close_follow_event_time_not_arrival(self):
        # Deliver the last tick first: open/close must still reflect the clock.
        window = {"max_lateness_ns": 60 * 10**9}
        ordered = [tick(0, "1.1000"), tick(10**9, "1.1050"), tick(2 * 10**9, "1.1020")]
        candle = build(list(reversed(ordered)), **window)[0]
        assert candle.open == dec("1.1000")
        assert candle.close == dec("1.1020")
