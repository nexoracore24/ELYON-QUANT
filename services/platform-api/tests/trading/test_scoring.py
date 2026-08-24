"""Scoring and explainability tests.

Two properties carry the most weight here. A veto must beat any score, because
that is the difference between a risk rule and a suggestion. And every decision
must explain itself from its own record -- the engine is never allowed to
answer "because it looked good".
"""

from __future__ import annotations

import pytest

from elyon.modules.trading.domain.explanation import (
    DecisionRecord,
    Provenance,
    explain,
)
from elyon.modules.trading.domain.scoring import (
    DEFAULT_WEIGHTS,
    Conviction,
    Factor,
    ScoreBuilder,
    Veto,
    max_possible,
    validate_weights,
)
from elyon.shared_kernel.edcs.numeric import DeterminismError

PROV = Provenance(data_version="eurusd-2026-07", config_hash="cfg-abc", dna_hash="dna-1")


def perfect_setup() -> ScoreBuilder:
    """Everything lines up -- the A+ case."""
    return (
        ScoreBuilder()
        .award(Factor.HTF_BIAS, "H4 bullish, aligned")
        .award(Factor.STRUCTURE, "M5 CHoCH with 1.8x ATR displacement")
        .award(Factor.LIQUIDITY_SWEEP, "swept equal lows, rejected")
        .award(Factor.POI_QUALITY, "unmitigated bullish order block")
        .award(Factor.IMBALANCE, "FVG in confluence")
        .award(Factor.PRICING, "discount at 0.29 of range")
        .award(Factor.OTE_FIBONACCI, "0.705 retracement")
        .award(Factor.VOLUME, "1.8x average")
        .award(Factor.TARGET_LIQUIDITY, "prior day high, RR 3.1")
    )


class TestWeights:
    def test_the_default_table_reads_as_a_percentage(self):
        assert max_possible() == 100
        validate_weights(DEFAULT_WEIGHTS)

    def test_context_factors_are_not_scored_here(self):
        # Killzone and volatility gate the scan; scoring them again would
        # count the same evidence twice (ADR-0008).
        names = {f.value for f in Factor}
        assert "KILLZONE" not in names
        assert "ATR_REGIME" not in names

    def test_a_table_that_cannot_reach_100_is_refused(self):
        with pytest.raises(DeterminismError, match="sum to 100"):
            validate_weights({Factor.HTF_BIAS: 50})


class TestScoring:
    def test_a_perfect_setup_scores_the_maximum(self):
        score = perfect_setup().build()
        assert score.total == 100
        assert score.conviction is Conviction.HIGH
        assert score.tradeable

    def test_the_total_is_exactly_the_sum_of_its_parts(self):
        score = perfect_setup().build()
        assert sum(f.awarded for f in score.factors) == score.total

    def test_partial_credit_is_supported(self):
        score = (
            ScoreBuilder()
            .award(Factor.POI_QUALITY, "block already tapped once", fraction="0.5")
            .build()
        )
        assert score.total == 7  # half of 14

    def test_a_withheld_factor_records_why_it_missed(self):
        score = (
            ScoreBuilder()
            .award(Factor.HTF_BIAS, "aligned")
            .withhold(Factor.IMBALANCE, "no FVG in the displacement")
            .build()
        )
        assert score.total == 17
        assert score.discarded[0].condition == "no FVG in the displacement"

    def test_scoring_a_factor_twice_is_a_bug(self):
        builder = ScoreBuilder().award(Factor.HTF_BIAS, "aligned")
        with pytest.raises(DeterminismError, match="scored twice"):
            builder.award(Factor.HTF_BIAS, "aligned again")

    def test_factor_order_is_stable(self):
        a = perfect_setup().build()
        b = perfect_setup().build()
        assert [f.factor for f in a.factors] == [f.factor for f in b.factors]

    def test_a_fraction_outside_the_unit_interval_is_refused(self):
        with pytest.raises(DeterminismError, match="within"):
            ScoreBuilder().award(Factor.HTF_BIAS, "x", fraction="1.5")


class TestConviction:
    def test_a_weak_setup_is_discarded(self):
        score = ScoreBuilder().award(Factor.HTF_BIAS, "aligned").build()
        assert score.conviction is Conviction.DISCARD
        assert not score.tradeable

    def test_a_near_miss_lands_on_the_watchlist(self):
        score = (
            ScoreBuilder()
            .award(Factor.HTF_BIAS, "aligned")             # 17
            .award(Factor.STRUCTURE, "CHoCH")              # 17
            .award(Factor.LIQUIDITY_SWEEP, "swept")        # 14
            .award(Factor.POI_QUALITY, "fresh block")      # 14
            .build()
        )
        assert score.total == 62
        assert score.conviction is Conviction.WATCHLIST
        assert not score.tradeable

    def test_crossing_the_threshold_makes_it_tradeable(self):
        score = (
            ScoreBuilder()
            .award(Factor.HTF_BIAS, "aligned")
            .award(Factor.STRUCTURE, "CHoCH")
            .award(Factor.LIQUIDITY_SWEEP, "swept")
            .award(Factor.POI_QUALITY, "fresh block")
            .award(Factor.IMBALANCE, "FVG")
            .build()
        )
        assert score.total == 74
        assert score.conviction is Conviction.STANDARD


class TestVetoes:
    def test_a_veto_beats_a_perfect_score(self):
        # The property that makes risk rules real rather than advisory.
        score = (
            perfect_setup()
            .check_veto(Veto.NEWS_WINDOW, True, "high-impact USD in 11 min")
            .build()
        )
        assert score.total == 100
        assert score.is_vetoed
        assert not score.tradeable
        assert score.conviction is Conviction.DISCARD

    def test_the_primary_reason_names_the_veto(self):
        score = (
            perfect_setup()
            .check_veto(Veto.SPREAD_BLOWOUT, True, "spread 4.2x typical")
            .build()
        )
        assert score.primary_reason == "veto:spread_blowout"

    def test_inactive_vetoes_are_recorded_as_checked(self):
        # Proving a rule was evaluated and passed matters for the audit trail.
        score = (
            perfect_setup()
            .check_veto(Veto.NEWS_WINDOW, False, "no events within 2h")
            .build()
        )
        assert not score.is_vetoed
        assert len(score.vetoes) == 1

    def test_below_threshold_reports_the_score_not_a_veto(self):
        score = ScoreBuilder().award(Factor.HTF_BIAS, "aligned").build()
        assert score.primary_reason == "score_below_threshold"


class TestDecisionRecord:
    def _record(self, score, action="enter_long") -> DecisionRecord:
        return DecisionRecord(
            symbol="EURUSD",
            bar_close_time_ns=1690000900000000000,
            side="LONG",
            action=action,
            score=score,
            provenance=PROV,
            detected={"trend": "H4 bullish", "atr": "0.00104"},
        )

    def test_the_decision_id_is_reproducible(self):
        a = self._record(perfect_setup().build())
        b = self._record(perfect_setup().build())
        assert a.decision_id == b.decision_id

    def test_a_different_config_yields_a_different_decision(self):
        base = self._record(perfect_setup().build())
        other = DecisionRecord(
            symbol="EURUSD",
            bar_close_time_ns=1690000900000000000,
            side="LONG",
            action="enter_long",
            score=perfect_setup().build(),
            provenance=Provenance("eurusd-2026-07", "cfg-DIFFERENT"),
        )
        assert base.decision_id != other.decision_id

    def test_provenance_travels_with_the_record(self):
        payload = self._record(perfect_setup().build()).to_canonical_dict()
        assert payload["dataVersion"] == "eurusd-2026-07"
        assert payload["configHash"] == "cfg-abc"
        assert payload["dnaHash"] == "dna-1"


class TestExplanation:
    def _record(self, score, action) -> DecisionRecord:
        return DecisionRecord(
            symbol="EURUSD",
            bar_close_time_ns=1690000900000000000,
            side="LONG",
            action=action,
            score=score,
            provenance=PROV,
            detected={"trend": "H4 bullish"},
        )

    def test_an_entry_explains_every_required_dimension(self):
        exp = explain(self._record(perfect_setup().build(), "enter_long"))
        assert exp.detected          # what it saw
        assert exp.confirmed         # what earned points
        assert exp.weights           # what each was worth
        assert exp.rules_fired       # which rules triggered
        assert exp.score == 100      # the total
        assert exp.primary_reason == "entered"

    def test_a_rejection_says_precisely_what_was_missing(self):
        score = (
            ScoreBuilder()
            .award(Factor.STRUCTURE, "M5 CHoCH")
            .award(Factor.LIQUIDITY_SWEEP, "swept lows")
            .withhold(Factor.IMBALANCE, "no FVG in the displacement")
            .withhold(Factor.PRICING, "price in premium, not discount")
            .check_veto(Veto.NEWS_WINDOW, True, "GBP high-impact in 11 min")
            .build()
        )
        exp = explain(self._record(score, "no_trade"))

        assert exp.primary_reason == "veto:news_window"
        assert any("no FVG" in d for d in exp.discarded)
        assert any("premium" in d for d in exp.discarded)
        assert any("GBP high-impact" in v for v in exp.vetoes_blocked)

    def test_the_narrative_never_says_because_it_looked_good(self):
        exp = explain(self._record(perfect_setup().build(), "enter_long"))
        text = exp.narrative.lower()
        assert "confirmed:" in text
        assert "reason:" in text
        # Concrete evidence, not vibes.
        assert "displacement" in text or "sweep" in text or "order block" in text

    def test_the_narrative_cites_only_factors_the_engine_weighed(self):
        exp = explain(self._record(perfect_setup().build(), "enter_long"))
        assert exp.cites_only({f.value for f in Factor})

    def test_an_explanation_cannot_misreport_the_total(self):
        # Guard against a future refactor letting the two drift apart.
        from dataclasses import replace
        score = perfect_setup().build()
        tampered = replace(score, total=score.total + 5)
        with pytest.raises(DeterminismError, match="misreport"):
            explain(self._record(tampered, "enter_long"))

    def test_the_score_is_reported_against_the_full_scale(self):
        # "71/71" would read as a perfect setup. The denominator is the scale.
        score = (
            ScoreBuilder()
            .award(Factor.HTF_BIAS, "aligned")
            .award(Factor.STRUCTURE, "CHoCH")
            .build()
        )
        exp = explain(self._record(score, "no_trade"))
        assert "score 34/100" in exp.narrative

    def test_a_perfect_score_still_reports_out_of_100(self):
        exp = explain(self._record(perfect_setup().build(), "enter_long"))
        assert "score 100/100" in exp.narrative

    def test_explanations_are_reproducible(self):
        a = explain(self._record(perfect_setup().build(), "enter_long"))
        b = explain(self._record(perfect_setup().build(), "enter_long"))
        assert a.narrative == b.narrative

    def test_a_no_trade_decision_is_explained_just_as_fully(self):
        # The discarded setups are where the learning is.
        score = ScoreBuilder().withhold(Factor.STRUCTURE, "no CHoCH yet").build()
        exp = explain(self._record(score, "no_trade"))
        assert exp.action == "no_trade"
        assert exp.discarded
        assert "not traded" in exp.narrative.lower()


class TestRiskHasTheLastWord:
    """A setup can clear the threshold and still not trade.

    The explanation must say so. Claiming an entry that never happened would
    be exactly the kind of drift the design exists to prevent.
    """

    def _rejected_by_risk(self) -> DecisionRecord:
        return DecisionRecord(
            symbol="EURUSD",
            bar_close_time_ns=1690000900000000000,
            side="LONG",
            action="no_trade",
            score=perfect_setup().build(),   # a full 100
            provenance=PROV,
            rejection_reason="risk:rr_below_minimum",
        )

    def test_the_narrative_does_not_claim_an_entry_that_never_happened(self):
        exp = explain(self._rejected_by_risk())
        assert "not traded" in exp.narrative.lower()
        assert "entered." not in exp.narrative.lower()

    def test_the_narrative_acknowledges_the_score_was_good(self):
        exp = explain(self._rejected_by_risk())
        assert "despite clearing the threshold" in exp.narrative.lower()

    def test_the_reason_names_the_rule_that_actually_stopped_it(self):
        exp = explain(self._rejected_by_risk())
        assert exp.primary_reason == "risk:rr_below_minimum"
        assert "risk:rr_below_minimum" in exp.narrative

    def test_the_record_reports_the_risk_reason_not_the_score_verdict(self):
        payload = self._rejected_by_risk().to_canonical_dict()
        assert payload["primaryReason"] == "risk:rr_below_minimum"
        assert payload["score"] == 100  # the score itself is unchanged

    def test_without_a_rejection_reason_the_score_verdict_stands(self):
        record = DecisionRecord(
            symbol="EURUSD", bar_close_time_ns=1, side="LONG",
            action="enter_long", score=perfect_setup().build(), provenance=PROV,
        )
        assert record.final_reason == "entered"
