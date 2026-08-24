"""Context Score and gate tests.

The gate answers the professional question -- *is this a market we should be
looking in?* -- before the retail one. Three properties matter most:

    1. A veto beats any score. Blown spread and dead feeds are conditions under
       which the number itself stops meaning anything.
    2. Context never scores the entry (ADR-0008). Counting killzone in both
       places pays twice for one piece of evidence.
    3. "We did not look" is as explainable as "we looked and declined".
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from elyon.modules.backtesting.domain import GeneratorConfig, generate
from elyon.modules.market_data.domain.atr import AtrProvider
from elyon.modules.market_data.domain.model import Candle, CandleState, Timeframe
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.market_context.domain import (
    CONTEXT_WEIGHTS,
    ContextBand,
    ContextConfig,
    ContextFactor,
    ContextVeto,
    GateResult,
    MarketRegime,
    NoCalendar,
    VolatilityRegime,
    profile_for,
    read_context,
    read_regime,
)
from elyon.modules.trading.domain.scoring import Factor as EntryFactor
from elyon.shared_kernel.edcs.numeric import ZERO, dec

SYMBOL = "EURUSD"
M1 = Timeframe.M1

MARKET = generate(GeneratorConfig(cycles=40))
EUR = profile_for(SYMBOL)


def atr_of(series: CandleSeries) -> object:
    provider = AtrProvider(period=14, output_scale=6)
    for candle in series:
        provider.update(candle)
    return provider.value or dec("0.001")


ATR = atr_of(MARKET)


def flat_series(bars: int = 60, price: str = "1.1000") -> CandleSeries:
    """A feed that has stopped moving."""
    p = dec(price)
    return CandleSeries.of([
        Candle(
            symbol=SYMBOL, timeframe=M1,
            open_time_ns=i * M1.duration_ns,
            close_time_ns=(i + 1) * M1.duration_ns,
            open=p, high=p, low=p, close=p,
            volume=dec("1"), tick_count=1, state=CandleState.CONFIRMED,
        )
        for i in range(bars)
    ])


def context(**kwargs):
    kwargs.setdefault("series", MARKET)
    kwargs.setdefault("atr", ATR)
    kwargs.setdefault("dna", EUR)
    series = kwargs.pop("series")
    atr = kwargs.pop("atr")
    dna = kwargs.pop("dna")
    return read_context(series, atr, dna, **kwargs)


class TestScoreComposition:
    def test_the_weights_reach_one_hundred(self):
        assert sum(CONTEXT_WEIGHTS.values()) == 100

    def test_every_factor_is_read(self):
        assert {f.factor for f in context().factors} == set(ContextFactor)

    def test_the_score_is_the_sum_of_its_parts(self):
        reading = context()
        assert sum(f.awarded for f in reading.factors) == reading.score

    def test_no_factor_can_exceed_its_weight(self):
        for factor in context().factors:
            assert 0 <= factor.awarded <= factor.weight

    def test_every_factor_explains_itself(self):
        for factor in context().factors:
            assert factor.detail.strip(), f"{factor.factor.value} said nothing"

    def test_reading_is_deterministic(self):
        a, b = context(), context()
        assert a.score == b.score
        assert a.gate is b.gate
        assert a.gate_reason == b.gate_reason


class TestContextNeverScoresTheEntry:
    """ADR-0008: the two factor sets are disjoint by construction."""

    def test_the_two_factor_sets_do_not_overlap(self):
        context_names = {f.value for f in ContextFactor}
        entry_names = {f.value for f in EntryFactor}
        assert not context_names & entry_names

    def test_context_produces_no_entry_points(self):
        # The reading carries a gate and a band, and nothing that could be
        # added to a setup's score.
        reading = context()
        assert not hasattr(reading, "entry_score")
        assert isinstance(reading.gate, GateResult)

    def test_volatility_is_gated_here_not_scored_there(self):
        assert ContextFactor.VOLATILITY in CONTEXT_WEIGHTS
        assert "ATR_REGIME" not in {f.value for f in EntryFactor}

    def test_session_is_gated_here_not_scored_there(self):
        assert ContextFactor.SESSION in CONTEXT_WEIGHTS
        assert "KILLZONE" not in {f.value for f in EntryFactor}


class TestVetoesBeatAnyScore:
    def test_a_blown_spread_fails_the_gate(self):
        reading = context(spread=EUR.max_spread * dec("3"))
        assert reading.gate is GateResult.FAIL
        assert ContextVeto.SPREAD_BLOWOUT in reading.blocking_vetoes

    def test_ungovernable_volatility_fails(self):
        # Not "a lower score" -- a refusal. At this ATR any sane stop is noise.
        reading = context(atr=EUR.typical_atr * dec("5"))
        assert ContextVeto.EXTREME_VOLATILITY in reading.blocking_vetoes
        assert reading.gate is GateResult.FAIL

    def test_extreme_volatility_can_be_allowed_deliberately(self):
        reading = context(
            atr=EUR.typical_atr * dec("5"),
            config=ContextConfig(allow_extreme_volatility=True),
        )
        assert ContextVeto.EXTREME_VOLATILITY not in reading.blocking_vetoes

    def test_a_dead_market_fails_too(self):
        # The opposite failure, and just as disqualifying: nothing to capture.
        reading = context(atr=EUR.typical_atr * dec("0.1"))
        assert ContextVeto.DEAD_MARKET in reading.blocking_vetoes

    def test_a_stopped_feed_fails(self):
        reading = context(series=flat_series(), atr=EUR.typical_atr)
        assert ContextVeto.STALE_DATA in reading.blocking_vetoes

    def test_too_little_data_fails_before_anything_is_computed(self):
        # A score derived from half a window is a number, not information.
        reading = context(series=MARKET.upto(10))
        assert reading.gate is GateResult.FAIL
        assert ContextVeto.INSUFFICIENT_DATA in reading.blocking_vetoes
        assert reading.score == 0
        assert reading.factors == ()

    def test_the_veto_is_named_in_the_gate_reason(self):
        reading = context(spread=EUR.max_spread * dec("3"))
        assert "veto:spread_blowout" in reading.gate_reason


class TestTheMissingCalendarIsVisible:
    """An unconnected data source must not hand out free points."""

    def test_news_is_withheld_rather_than_assumed_clear(self):
        reading = context()
        news = next(
            f for f in reading.factors if f.factor is ContextFactor.NEWS_CLEAR
        )
        assert not news.satisfied
        assert news.awarded == 0
        assert "no economic calendar" in news.detail

    def test_the_ceiling_drops_by_exactly_the_missing_weight(self):
        # Scoring out of 92 while reporting out of 100 would understate how
        # much the system does not know.
        assert NoCalendar().is_blocked(SYMBOL, 0) is False
        reachable = 100 - CONTEXT_WEIGHTS[ContextFactor.NEWS_CLEAR]
        assert reachable == 92
        assert context().score <= reachable

    def test_a_real_calendar_can_award_it(self):
        class ClearCalendar:
            def is_blocked(self, symbol, at_ns): return False
            def describe(self, symbol, at_ns): return "checked, nothing due"

        reading = context(calendar=ClearCalendar())
        news = next(
            f for f in reading.factors if f.factor is ContextFactor.NEWS_CLEAR
        )
        assert news.satisfied

    def test_a_real_calendar_can_also_veto(self):
        class BlockingCalendar:
            def is_blocked(self, symbol, at_ns): return True
            def describe(self, symbol, at_ns): return "USD NFP in 4 minutes"

        reading = context(calendar=BlockingCalendar())
        assert ContextVeto.NEWS_BLACKOUT in reading.blocking_vetoes
        assert "NFP" in reading.gate_reason


class TestBandsAndGate:
    def _forced(self, score: int):
        """A reading with its score overridden, to test banding in isolation."""
        return replace(context(), score=score)

    def test_the_bands_partition_the_range(self):
        assert self._forced(30).band is ContextBand.POOR
        assert self._forced(50).band is ContextBand.MARGINAL
        assert self._forced(70).band is ContextBand.TRADEABLE
        assert self._forced(85).band is ContextBand.EXCELLENT

    def test_a_marginal_context_does_not_scan(self):
        assert not replace(
            self._forced(55), gate=GateResult.FAIL
        ).should_scan

    def test_the_threshold_comes_from_the_instrument(self):
        assert context().threshold == EUR.context_threshold

    def test_a_failing_gate_lists_what_was_missing(self):
        reading = context()
        if reading.gate is GateResult.FAIL and not reading.vetoes:
            assert "missing" in reading.gate_reason

    def test_the_gate_reason_is_never_empty(self):
        # "We did not look" has to be as explainable as "we looked and declined".
        for reading in (context(), context(spread=EUR.max_spread * dec("3")),
                        context(series=MARKET.upto(10))):
            assert reading.gate_reason.strip()


class TestHysteresis:
    """A market resting on the line must not switch the engine on and off."""

    def _at(self, score: int, gate: GateResult):
        return replace(context(), score=score, gate=gate)

    def test_an_open_gate_survives_a_small_dip(self):
        config = ContextConfig(hysteresis=5)
        passing = self._at(70, GateResult.PASS)
        # Same bars, but now the gate remembers it was open.
        cold = read_context(MARKET, ATR, EUR, config=config)
        warm = read_context(MARKET, ATR, EUR, config=config, previous=passing)
        if cold.score >= EUR.context_threshold - 5:
            assert warm.gate is GateResult.PASS

    def test_hysteresis_never_defeats_a_veto(self):
        # Being open a moment ago is not a reason to keep trading through a
        # blown spread.
        passing = self._at(90, GateResult.PASS)
        reading = read_context(
            MARKET, ATR, EUR, spread=EUR.max_spread * dec("3"), previous=passing
        )
        assert reading.gate is GateResult.FAIL

    def test_a_closed_gate_gets_no_discount(self):
        closed = self._at(20, GateResult.FAIL)
        strict = read_context(MARKET, ATR, EUR)
        lenient = read_context(MARKET, ATR, EUR, previous=closed)
        assert strict.gate is lenient.gate


class TestRegimeReading:
    def test_churn_is_not_tradeable(self):
        # It looks like activity, which is what makes it expensive.
        assert not MarketRegime.CHURN.is_tradeable

    def test_a_dead_market_is_not_tradeable(self):
        assert not VolatilityRegime.DEAD.is_tradeable
        assert not VolatilityRegime.EXTREME.is_tradeable

    def test_quiet_is_thin_but_allowed(self):
        assert VolatilityRegime.QUIET.is_tradeable
        assert not VolatilityRegime.QUIET.is_ideal

    def test_the_reading_explains_itself(self):
        reading = read_regime(MARKET, ATR, EUR)
        assert "volatility" in reading.detail
        assert str(reading.efficiency) in reading.detail

    def test_a_flat_market_reads_as_dead(self):
        reading = read_regime(flat_series(), EUR.typical_atr * dec("0.05"), EUR)
        assert reading.volatility is VolatilityRegime.DEAD
        assert not reading.is_tradeable

    def test_too_little_data_is_undetermined_not_guessed(self):
        reading = read_regime(MARKET.upto(2), ATR, EUR)
        assert reading.regime is MarketRegime.UNDETERMINED


class TestProvenance:
    def test_the_dna_hash_travels_with_the_reading(self):
        assert context().dna_hash == EUR.dna_hash

    def test_an_uncalibrated_profile_is_flagged_on_every_reading(self):
        # The whole engine is running on guessed thresholds until a profile is
        # learned, and that should never be invisible.
        assert not context().dna_calibrated

    def test_the_summary_shows_the_scale_and_the_verdict(self):
        summary = context().summary()
        assert "threshold" in summary
        assert any(g.value in summary for g in GateResult)
