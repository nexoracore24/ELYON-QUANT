"""Report metrics, and the loop closing.

The loop is the point of the whole module: a strategy ships UNPROVEN and cannot
trade alone, a backtest measures it, and the measurement -- not anyone's opinion
-- decides what it is allowed to do. The last class here walks that end to end.
"""

from __future__ import annotations

import pytest

from elyon.modules.backtesting.domain import (
    ExitReason,
    Sample,
    SimulatedTrade,
    calibration_from,
    report_from,
    tier_of,
)
from elyon.modules.smart_money.domain.structure import Direction
from elyon.modules.strategy.domain import (
    Calibration,
    GateResult,
    PlaybookConfig,
    ProbabilityTier,
    StrategyId,
    StrategyRegistry,
)
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec

HOUSE = StrategyId.SIX_PILLARS


def trade(r: str, *, index: int = 0, reason=ExitReason.TARGET) -> SimulatedTrade:
    """A trade engineered to book exactly ``r`` R.

    Risk is fixed at 0.0010, so the exit price is entry + r * risk.
    """
    risk = dec("0.0010")
    entry = dec("1.1000")
    return SimulatedTrade(
        strategy=HOUSE, direction=Direction.UP, signal_index=index,
        entry_index=index, exit_index=index + 1,
        entry=entry, stop=entry - risk, target=entry + risk * dec(2),
        exit_price=entry + risk * dec(r), reason=reason, risk=risk,
    )


def report(*rs: str, sample=Sample.OUT_OF_SAMPLE, dataset="test"):
    return report_from(
        [trade(r, index=i) for i, r in enumerate(rs)],
        strategy=HOUSE, dataset=dataset, sample=sample,
        data_hash="d" * 16, config_hash="c" * 16, registry_hash="r" * 16,
    )


class TestMetrics:
    def test_expectancy_is_the_mean_r(self):
        assert report("2", "-1", "2", "-1").expectancy_r == dec("0.5")

    def test_win_rate_counts_positive_trades(self):
        assert report("2", "-1", "2", "-1").win_rate == dec("0.5")

    def test_a_break_even_trade_is_not_a_win(self):
        assert report("0", "0", "2").wins == 1

    def test_total_is_the_sum(self):
        assert report("2", "-1", "3").total_r == dec("4")

    def test_average_win_and_loss_are_separated(self):
        run = report("3", "1", "-1", "-1")
        assert run.average_win == dec("2")
        assert run.average_loss == dec("-1")

    def test_profit_factor_is_gross_win_over_gross_loss(self):
        assert report("3", "1", "-2").profit_factor == dec("2")

    def test_a_run_with_no_losses_has_no_profit_factor(self):
        # Not infinity -- too few trades. Returning a number here is how a
        # three-trade sample ends up quoted in a pitch deck.
        assert report("2", "3").profit_factor is None

    def test_an_empty_run_does_not_divide_by_zero(self):
        empty = report()
        assert empty.expectancy_r == ZERO
        assert empty.win_rate == ZERO
        assert empty.max_drawdown_r == ZERO

    def test_the_equity_curve_accumulates(self):
        assert report("1", "-1", "2").equity_curve == (dec("1"), ZERO, dec("2"))

    def test_max_drawdown_measures_peak_to_trough(self):
        # Up to 3, down to 0: the worst fall is -3, not the -1 of any one trade.
        assert report("3", "-1", "-1", "-1", "2").max_drawdown_r == dec("-3")

    def test_drawdown_is_zero_when_nothing_falls(self):
        assert report("1", "1", "1").max_drawdown_r == ZERO

    def test_the_worst_streak_is_what_gets_a_system_switched_off(self):
        assert report("1", "-1", "-1", "-1", "1", "-1").longest_losing_streak == 3

    def test_exits_are_counted_by_reason(self):
        run = report_from(
            [
                trade("2", index=0, reason=ExitReason.TARGET),
                trade("-1", index=1, reason=ExitReason.STOP),
                trade("-2", index=2, reason=ExitReason.GAP_THROUGH_STOP),
            ],
            strategy=HOUSE, dataset="t", sample=Sample.OUT_OF_SAMPLE,
            data_hash="d", config_hash="c", registry_hash="r",
        )
        assert run.by_reason() == {
            ExitReason.TARGET: 1,
            ExitReason.STOP: 1,
            ExitReason.GAP_THROUGH_STOP: 1,
        }

    def test_unresolved_trades_are_counted(self):
        run = report_from(
            [trade("1", index=0, reason=ExitReason.END_OF_DATA)],
            strategy=HOUSE, dataset="t", sample=Sample.OUT_OF_SAMPLE,
            data_hash="d", config_hash="c", registry_hash="r",
        )
        assert run.unresolved == 1


class TestOutlierDetection:
    """The most useful sanity check in the report."""

    def test_expectancy_without_the_best_trade_is_reported(self):
        # 20R, then four -1R. The mean says +3.2R; without the outlier it is -1R.
        run = report("20", "-1", "-1", "-1", "-1")
        assert run.expectancy_r == dec("3.2")
        assert run.expectancy_ex_best == dec("-1")

    def test_a_run_carried_by_one_trade_is_flagged(self):
        # An edge that vanishes when one trade is removed is not an edge, it is
        # an outlier, and outliers do not repeat on schedule.
        assert report("20", "-1", "-1", "-1", "-1").carried_by_one_trade

    def test_a_genuinely_broad_edge_is_not_flagged(self):
        assert not report("2", "2", "-1", "2", "-1", "2").carried_by_one_trade

    def test_a_losing_run_is_not_flagged_as_carried(self):
        # The flag means "looks profitable but is not". A run that already
        # looks unprofitable needs no such warning.
        assert not report("1", "-2", "-2").carried_by_one_trade

    def test_the_warning_appears_in_the_summary(self):
        assert "carried by one trade" in report("20", "-1", "-1", "-1").summary()

    def test_a_single_trade_run_has_no_ex_best_figure(self):
        assert report("5").expectancy_ex_best == ZERO


class TestSampleHonesty:
    def test_in_sample_cannot_certify(self):
        with pytest.raises(DeterminismError, match="in-sample"):
            calibration_from(report("2", "2", "2", sample=Sample.IN_SAMPLE))

    def test_out_of_sample_certifies(self):
        run = report(*(["2", "-1"] * 20), sample=Sample.OUT_OF_SAMPLE)
        assert calibration_from(run).sample_size == 40

    def test_forward_data_certifies_too(self):
        # Shadow-mode live data is the strongest evidence there is.
        run = report(*(["2", "-1"] * 20), sample=Sample.FORWARD)
        assert calibration_from(run).sample_size == 40

    def test_the_certified_record_matches_the_report(self):
        run = report(*(["2", "-1"] * 20), sample=Sample.OUT_OF_SAMPLE)
        record = calibration_from(run)
        assert record.wins == run.wins
        assert record.expectancy_r == run.expectancy_r
        assert record.max_drawdown_r == run.max_drawdown_r

    def test_a_preview_tier_needs_no_certification(self):
        run = report(*(["2", "-1"] * 20), sample=Sample.IN_SAMPLE)
        assert tier_of(run) is ProbabilityTier.HIGH


class TestTheLoopCloses:
    """From "ships unproven" to "allowed to trade", end to end."""

    def _measured(self, *rs: str) -> Calibration:
        return calibration_from(report(*rs, sample=Sample.OUT_OF_SAMPLE))

    def test_a_strategy_starts_unable_to_trade_alone(self):
        assert PlaybookConfig().tier_of(HOUSE) is ProbabilityTier.UNPROVEN

    def test_a_good_measurement_unlocks_it(self):
        # 40 trades, half winning 2R and half losing 1R -> +0.5R expectancy.
        config = PlaybookConfig(calibrations={HOUSE: self._measured(*(["2", "-1"] * 20))})
        assert config.tier_of(HOUSE) is ProbabilityTier.HIGH
        assert config.tier_of(HOUSE).corroboration_required == 0

    def test_a_marginal_measurement_only_half_unlocks_it(self):
        # +0.2R over 40 trades: real, but it still needs corroboration.
        config = PlaybookConfig(
            calibrations={HOUSE: self._measured(*(["1.4", "-1"] * 20))}
        )
        assert config.tier_of(HOUSE) is ProbabilityTier.MEDIUM
        assert config.tier_of(HOUSE).corroboration_required == 1

    def test_a_bad_measurement_locks_it_down(self):
        config = PlaybookConfig(
            calibrations={HOUSE: self._measured(*(["1", "-2"] * 20))}
        )
        assert config.tier_of(HOUSE) is ProbabilityTier.LOW

    def test_a_short_measurement_changes_nothing(self):
        # Ten trades is a story. The strategy stays exactly where it started.
        config = PlaybookConfig(calibrations={HOUSE: self._measured(*(["3"] * 10))})
        assert config.tier_of(HOUSE) is ProbabilityTier.UNPROVEN

    def test_the_evidence_names_the_data_it_came_from(self):
        # Otherwise "which data was this measured on?" is unanswerable later,
        # which is the same as having no evidence at all.
        record = calibration_from(
            report(*(["2", "-1"] * 20), sample=Sample.OUT_OF_SAMPLE,
                   dataset="eurusd-2024-h2")
        )
        assert record.dataset.startswith("eurusd-2024-h2:")
