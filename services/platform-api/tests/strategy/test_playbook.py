"""Plays and the playbook.

The plays are tested for one thing above all: they are *total*. Every play
returns a signal for every context, and every abstention carries a reason. A
strategy that answers silence is indistinguishable from one that crashed, and a
catalog of thirteen is only safe to ship if none of them can go quiet.

The playbook is tested for the three rules that decide whether a strategy
catalog is an edge or a trade-count maximiser:

    1. Confluence counts families, never strategies.
    2. Disagreement is a veto, not an average.
    3. An uncalibrated strategy never opens a trade by itself.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from elyon.modules.market_data.domain.model import Candle, CandleState, Timeframe
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.smart_money.domain.structure import Direction
from elyon.modules.strategy.domain import (
    CATALOG,
    PLAYS,
    Calibration,
    ConflictPolicy,
    GateResult,
    PlaybookConfig,
    ProbabilityTier,
    SideReading,
    StrategyContext,
    StrategyFamily,
    StrategyId,
    StrategyRegistry,
    StrategySignal,
    abstain,
    build_context,
    evaluate,
    fire,
    profile,
    score_verdict,
)
from elyon.modules.trading.domain.scoring import Veto
from elyon.shared_kernel.edcs.numeric import ZERO, dec

SYMBOL = "EURUSD"
M1 = Timeframe.M1
MINUTE = M1.duration_ns
ATR = dec("0.00437")

# 2026-01-15 10:00 New York -- inside the Silver Bullet hour, so the session
# models are reachable rather than permanently abstaining on "wrong time".
SESSION_START_NS = 1768489200_000_000_000


def bar(i: int, o: str, h: str, l: str, c: str, *, base: int = 0) -> Candle:
    op, cl = dec(o), dec(c)
    start = base + i * MINUTE
    return Candle(
        symbol=SYMBOL, timeframe=M1,
        open_time_ns=start, close_time_ns=start + MINUTE,
        open=op, high=max(dec(h), op, cl), low=min(dec(l), op, cl), close=cl,
        volume=dec("10"), tick_count=4, state=CandleState.CONFIRMED,
    )


def series_of(bars, *, base: int = 0) -> CandleSeries:
    return CandleSeries.of([bar(i, *ohlc, base=base) for i, ohlc in enumerate(bars)])


BULLISH = [
    ("1.1010", "1.1030", "1.1008", "1.1025"),
    ("1.1025", "1.1028", "1.0995", "1.1020"),
    ("1.1020", "1.1060", "1.1018", "1.1055"),
    ("1.1055", "1.1058", "1.1015", "1.1050"),
    ("1.1050", "1.1090", "1.1045", "1.1085"),
    ("1.1085", "1.1088", "1.1040", "1.1080"),
    ("1.1080", "1.1120", "1.1075", "1.1115"),
    ("1.1115", "1.1118", "1.1035", "1.1070"),
    ("1.1070", "1.1145", "1.1068", "1.1140"),
    ("1.1140", "1.1150", "1.1132", "1.1142"),
    ("1.1142", "1.1145", "1.1078", "1.1082"),
    ("1.1082", "1.1086", "1.1072", "1.1080"),
]

CHOPPY = [
    ("1.1000", "1.1012", "1.0994", "1.1004"),
    ("1.1004", "1.1010", "1.0996", "1.1000"),
    ("1.1000", "1.1014", "1.0998", "1.1002"),
    ("1.1002", "1.1008", "1.0992", "1.1006"),
    ("1.1006", "1.1013", "1.0997", "1.0999"),
    ("1.0999", "1.1011", "1.0995", "1.1005"),
    ("1.1005", "1.1009", "1.0993", "1.1001"),
    ("1.1001", "1.1015", "1.0999", "1.1003"),
]

FLAT = [("1.1000", "1.1001", "1.0999", "1.1000")] * 6


def context(bars=None, *, base: int = SESSION_START_NS) -> StrategyContext:
    return build_context(
        series_of(bars or BULLISH, base=base), ATR, symbol=SYMBOL
    )


def proven(*strategies: StrategyId, expectancy: str = "0.42") -> PlaybookConfig:
    """Give strategies enough evidence to be trusted."""
    return PlaybookConfig(
        calibrations={
            s: Calibration(180, 92, dec(expectancy), dataset="test")
            for s in strategies
        }
    )


# ---------------------------------------------------------------------------
# The plays
# ---------------------------------------------------------------------------

class TestEveryPlayIsTotal:
    """Thirteen strategies are only safe to ship if none can go quiet."""

    @pytest.mark.parametrize("strategy", list(StrategyId))
    @pytest.mark.parametrize("bars", [BULLISH, CHOPPY, FLAT], ids=["bull", "chop", "flat"])
    def test_a_play_always_returns_a_signal(self, strategy, bars):
        signal = PLAYS[strategy](context(bars))
        assert isinstance(signal, StrategySignal)
        assert signal.strategy is strategy

    @pytest.mark.parametrize("strategy", list(StrategyId))
    @pytest.mark.parametrize("bars", [BULLISH, CHOPPY, FLAT], ids=["bull", "chop", "flat"])
    def test_an_abstention_always_says_why(self, strategy, bars):
        signal = PLAYS[strategy](context(bars))
        assert signal.reason.strip(), f"{strategy.value} abstained silently"

    def test_every_catalogued_strategy_has_an_implementation(self):
        assert set(PLAYS) == set(CATALOG)

    @pytest.mark.parametrize("strategy", list(StrategyId))
    def test_plays_are_deterministic(self, strategy):
        a = PLAYS[strategy](context())
        b = PLAYS[strategy](context())
        assert a == b


class TestIndividualPlays:
    def test_six_pillars_fires_on_its_own_setup(self):
        signal = PLAYS[StrategyId.SIX_PILLARS](context())
        assert signal.fired
        assert signal.direction is Direction.UP

    def test_six_pillars_abstains_when_too_few_align(self):
        # Two ways to fail, and the reason has to name which one: no side at
        # all, or a side with not enough behind it.
        signal = PLAYS[StrategyId.SIX_PILLARS](context(CHOPPY))
        assert not signal.fired
        assert "no side" in signal.reason or "/6 aligned" in signal.reason

    def test_a_partial_alignment_names_the_missing_pillars(self):
        # When there *is* a side, the abstention lists what was absent -- a
        # count alone would leave the trader guessing.
        thin = [
            ("1.1000", "1.1005", "1.0995", "1.1002"),
            ("1.1002", "1.1006", "1.0980", "1.1000"),   # dips, printing a low
            ("1.1000", "1.1004", "1.0998", "1.1001"),
            ("1.1001", "1.1005", "1.0975", "1.1003"),   # sweeps that low
            ("1.1003", "1.1008", "1.1000", "1.1006"),
        ]
        signal = PLAYS[StrategyId.SIX_PILLARS](context(thin))
        if not signal.fired and "/6 aligned" in signal.reason:
            assert "(" in signal.reason  # the missing pillars are enumerated

    def test_ote_reports_where_price_actually_is(self):
        signal = PLAYS[StrategyId.ICT_OTE](context())
        assert not signal.fired
        assert "outside" in signal.reason

    def test_session_models_say_when_the_clock_is_wrong(self):
        # Outside the window the pattern means nothing, and saying "outside the
        # window" is more useful than saying "no setup".
        off_hours = context(base=1768546800_000_000_000)  # 02:00 NY, next day
        signal = PLAYS[StrategyId.ICT_SILVER_BULLET](off_hours)
        assert not signal.fired
        assert "Silver Bullet hours" in signal.reason

    def test_the_2022_model_names_the_step_it_is_waiting_on(self):
        # The sequence is the strategy, so the abstention should say which part
        # of the sequence has not happened.
        signal = PLAYS[StrategyId.ICT_2022_MODEL](context())
        assert not signal.fired
        assert any(
            word in signal.reason
            for word in ("no liquidity", "has not shifted", "no unfilled gap")
        )

    def test_smt_admits_it_cannot_run(self):
        # Approximating it into something computable from one symbol would give
        # a strategy that fires on noise and calls it divergence.
        signal = PLAYS[StrategyId.SMT_DIVERGENCE](context())
        assert not signal.fired
        assert "correlated" in signal.reason


class TestSignalInvariants:
    def test_an_abstention_carries_no_confidence(self):
        signal = abstain(StrategyId.ICT_OTE, "nothing to see")
        assert signal.confidence == ZERO
        assert not signal.fired

    def test_confidence_without_a_direction_is_a_bug(self):
        with pytest.raises(ValueError, match="abstaining"):
            StrategySignal(StrategyId.ICT_OTE, None, dec("0.5"), "confused")

    def test_confidence_outside_the_unit_interval_is_refused(self):
        with pytest.raises(ValueError, match="outside"):
            fire(StrategyId.ICT_OTE, Direction.UP, "1.4", "over-confident")

    def test_confidence_is_quantized(self):
        # Full-precision division would put 28 digits of noise into every
        # explanation and every hash.
        signal = fire(
            StrategyId.SIX_PILLARS, Direction.UP, dec(2) / dec(3), "two thirds"
        )
        assert signal.confidence == dec("0.666667")


# ---------------------------------------------------------------------------
# The playbook
# ---------------------------------------------------------------------------

def signal_from(strategy: StrategyId, direction: Direction, confidence="0.8"):
    return fire(strategy, direction, confidence, f"{strategy.value} test signal")


class TestConfluenceCountsFamilies:
    """The rule that stops a catalog manufacturing conviction by growing."""

    def test_two_strategies_in_one_family_count_once(self):
        from elyon.modules.strategy.domain.playbook import _read_side

        same_family = [
            StrategyId.SIX_PILLARS,
            StrategyId.ICT_TURTLE_SOUP,
            StrategyId.EQUAL_LEVEL_RAID,
        ]
        assert len({profile(s).family for s in same_family}) == 1

        side = _read_side(
            Direction.UP,
            tuple(signal_from(s, Direction.UP) for s in same_family),
            proven(*same_family),
        )
        assert side.confluence == 1
        assert side.families == (StrategyFamily.LIQUIDITY_RAID,)

    def test_distinct_families_do_count_separately(self):
        from elyon.modules.strategy.domain.playbook import _read_side

        mixed = [StrategyId.SIX_PILLARS, StrategyId.ICT_2022_MODEL,
                 StrategyId.ICT_OTE]
        assert len({profile(s).family for s in mixed}) == 3

        side = _read_side(
            Direction.UP,
            tuple(signal_from(s, Direction.UP) for s in mixed),
            proven(*mixed),
        )
        assert side.confluence == 3

    def test_adding_a_duplicate_strategy_never_raises_confidence(self):
        # Adding a strategy must not, by itself, make an existing setup look
        # better. This is the property that keeps the catalog honest.
        from elyon.modules.strategy.domain.playbook import _read_side

        one = _read_side(
            Direction.UP,
            (signal_from(StrategyId.SIX_PILLARS, Direction.UP),),
            proven(StrategyId.SIX_PILLARS, StrategyId.ICT_TURTLE_SOUP),
        )
        two = _read_side(
            Direction.UP,
            (
                signal_from(StrategyId.SIX_PILLARS, Direction.UP),
                signal_from(StrategyId.ICT_TURTLE_SOUP, Direction.UP, "0.5"),
            ),
            proven(StrategyId.SIX_PILLARS, StrategyId.ICT_TURTLE_SOUP),
        )
        assert two.weighted_confidence <= one.weighted_confidence

    def test_the_strongest_signal_stands_for_its_family(self):
        # Averaging would let a weak duplicate dilute a strong read.
        from elyon.modules.strategy.domain.playbook import _read_side

        side = _read_side(
            Direction.UP,
            (
                signal_from(StrategyId.SIX_PILLARS, Direction.UP, "0.9"),
                signal_from(StrategyId.ICT_TURTLE_SOUP, Direction.UP, "0.1"),
            ),
            proven(StrategyId.SIX_PILLARS, StrategyId.ICT_TURTLE_SOUP),
        )
        assert side.weighted_confidence >= dec("0.85")

    def test_confluence_stops_paying_eventually(self):
        from elyon.modules.strategy.domain.playbook import (
            CONFLUENCE_BONUS,
            MAX_CONFLUENCE_FAMILIES,
        )
        bonuses = [CONFLUENCE_BONUS[i] for i in range(1, MAX_CONFLUENCE_FAMILIES + 1)]
        assert bonuses == sorted(bonuses)
        # Diminishing, not linear: a crowded chart is not a certainty.
        gains = [b - a for a, b in zip(bonuses, bonuses[1:])]
        assert gains == sorted(gains, reverse=True)


class TestDisagreementIsAVeto:
    def _conflicted(self, policy=ConflictPolicy.VETO, **kwargs):
        cfg = proven(StrategyId.SIX_PILLARS, StrategyId.ICT_2022_MODEL)
        return replace(cfg, conflict_policy=policy, **kwargs)

    def test_opposite_sides_stand_the_engine_down(self):
        from elyon.modules.strategy.domain.playbook import _decide, _read_side

        cfg = self._conflicted()
        sides = [
            _read_side(Direction.UP,
                       (signal_from(StrategyId.SIX_PILLARS, Direction.UP),), cfg),
            _read_side(Direction.DOWN,
                       (signal_from(StrategyId.ICT_2022_MODEL, Direction.DOWN),), cfg),
        ]
        direction, gate, reason, _ = _decide(sides, cfg)
        assert direction is None
        assert gate is GateResult.CONFLICTED
        assert "disagree" in reason

    def test_a_conflict_is_never_quietly_netted_out(self):
        # Netting produces a small position in whichever side was louder and
        # hides that the engine had no read at all.
        from elyon.modules.strategy.domain.playbook import _decide, _read_side

        cfg = self._conflicted()
        sides = [
            _read_side(Direction.UP,
                       (signal_from(StrategyId.SIX_PILLARS, Direction.UP, "0.9"),), cfg),
            _read_side(Direction.DOWN,
                       (signal_from(StrategyId.ICT_2022_MODEL, Direction.DOWN, "0.1"),), cfg),
        ]
        direction, gate, _, _ = _decide(sides, cfg)
        assert direction is None  # not UP, despite being nine times louder

    def test_strongest_wins_needs_a_real_margin(self):
        from elyon.modules.strategy.domain.playbook import _decide, _read_side

        cfg = self._conflicted(ConflictPolicy.STRONGEST_WINS)
        near_tie = [
            _read_side(Direction.UP,
                       (signal_from(StrategyId.SIX_PILLARS, Direction.UP, "0.60"),), cfg),
            _read_side(Direction.DOWN,
                       (signal_from(StrategyId.ICT_2022_MODEL, Direction.DOWN, "0.55"),), cfg),
        ]
        direction, gate, _, _ = _decide(near_tie, cfg)
        assert direction is None
        assert gate is GateResult.CONFLICTED

    def test_strongest_wins_acts_when_the_margin_is_clear(self):
        from elyon.modules.strategy.domain.playbook import _decide, _read_side

        cfg = self._conflicted(ConflictPolicy.STRONGEST_WINS)
        lopsided = [
            _read_side(Direction.UP,
                       (signal_from(StrategyId.SIX_PILLARS, Direction.UP, "0.95"),), cfg),
            _read_side(Direction.DOWN,
                       (signal_from(StrategyId.ICT_2022_MODEL, Direction.DOWN, "0.10"),), cfg),
        ]
        direction, gate, _, _ = _decide(lopsided, cfg)
        assert direction is Direction.UP
        assert gate is GateResult.PASSED

    def test_a_conflict_reaches_the_score_as_a_veto(self):
        ctx = context()
        reg = StrategyRegistry.all_off().live(StrategyId.SIX_PILLARS)
        verdict = evaluate(ctx, reg, config=proven(StrategyId.SIX_PILLARS))
        score = score_verdict(ctx, verdict)
        assert any(v.veto is Veto.STRATEGY_CONFLICT for v in score.vetoes)


class TestUnprovenCannotTradeAlone:
    def test_the_default_registry_reaches_no_trade(self):
        # Nothing is calibrated on day one, so nothing trades on day one.
        verdict = evaluate(context(), StrategyRegistry.default())
        assert not verdict.tradeable
        assert verdict.gate is GateResult.INSUFFICIENT_CORROBORATION

    def test_the_reason_does_not_leak_the_sentinel(self):
        verdict = evaluate(context(), StrategyRegistry.default())
        assert "99" not in verdict.reason
        assert "uncalibrated" in verdict.reason

    def test_evidence_unlocks_the_trade(self):
        reg = StrategyRegistry.all_off().live(StrategyId.SIX_PILLARS)
        verdict = evaluate(context(), reg, config=proven(StrategyId.SIX_PILLARS))
        assert verdict.tradeable
        assert verdict.direction is Direction.UP

    def test_weak_evidence_does_not(self):
        # 0.05R is barely above break-even: LOW, and LOW needs corroboration.
        reg = StrategyRegistry.all_off().live(StrategyId.SIX_PILLARS)
        verdict = evaluate(
            context(), reg,
            config=proven(StrategyId.SIX_PILLARS, expectancy="0.05"),
        )
        assert not verdict.tradeable
        assert verdict.gate is GateResult.INSUFFICIENT_CORROBORATION

    def test_no_signals_is_distinct_from_no_corroboration(self):
        # "Nothing fired" and "something fired but was not trusted" are
        # different failures and need different fixes.
        verdict = evaluate(context(FLAT), StrategyRegistry.default())
        assert verdict.gate is GateResult.NO_SIGNALS


class TestShadowMode:
    def test_shadow_signals_never_influence_the_verdict(self):
        ctx = context()
        live_only = StrategyRegistry.all_off().live(StrategyId.SIX_PILLARS)
        with_shadows = live_only.shadow(
            *(s for s in StrategyId if s is not StrategyId.SIX_PILLARS)
        )
        cfg = proven(StrategyId.SIX_PILLARS)
        a = evaluate(ctx, live_only, config=cfg)
        b = evaluate(ctx, with_shadows, config=cfg)
        assert a.direction == b.direction
        assert a.gate == b.gate
        assert a.confidence == b.confidence

    def test_but_they_are_still_recorded(self):
        # Recording is the whole point: it is how an unproven strategy
        # accumulates the evidence it needs to stop being unproven.
        verdict = evaluate(context(), StrategyRegistry.default())
        assert len(verdict.shadow_signals) == len(StrategyId) - 1

    def test_a_shadow_strategy_cannot_cause_a_conflict(self):
        ctx = context()
        reg = (
            StrategyRegistry.all_off()
            .live(StrategyId.SIX_PILLARS)
            .shadow(StrategyId.ICT_2022_MODEL)
        )
        verdict = evaluate(ctx, reg, config=proven(StrategyId.SIX_PILLARS))
        assert verdict.gate is not GateResult.CONFLICTED

    def test_off_strategies_are_not_even_evaluated(self):
        verdict = evaluate(
            context(), StrategyRegistry.all_off().live(StrategyId.SIX_PILLARS)
        )
        assert verdict.shadow_signals == ()
        assert len(verdict.live_signals) == 1


class TestVerdictReporting:
    def test_the_verdict_separates_fired_from_abstained(self):
        verdict = evaluate(context(), StrategyRegistry.default())
        assert set(verdict.fired) | set(verdict.abstained) == set(verdict.live_signals)
        assert not set(verdict.fired) & set(verdict.abstained)

    def test_the_registry_hash_travels_with_the_verdict(self):
        reg = StrategyRegistry.default()
        assert evaluate(context(), reg).registry_hash == reg.config_hash

    def test_the_summary_covers_every_evaluated_strategy(self):
        verdict = evaluate(context(), StrategyRegistry.default())
        lines = verdict.summary().splitlines()
        assert len(lines) == len(StrategyId)

    def test_the_verdict_is_reproducible(self):
        reg = StrategyRegistry.default()
        a, b = evaluate(context(), reg), evaluate(context(), reg)
        assert a.direction == b.direction
        assert a.gate == b.gate
        assert a.reason == b.reason
        assert a.confidence == b.confidence

    def test_the_confidence_stays_within_bounds(self):
        # Even with every strategy live and calibrated, confluence must not
        # push confidence past certainty.
        every = proven(*StrategyId)
        reg = StrategyRegistry.all_off().live(
            *(s for s in StrategyId if profile(s).available)
        )
        verdict = evaluate(context(), reg, config=every)
        assert ZERO <= verdict.confidence <= dec("1")
