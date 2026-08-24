"""Turning simulated trades into the evidence a tier rests on.

This module closes the loop the strategy catalog was built around: a strategy
ships UNPROVEN, runs in shadow or in a backtest, and the record produced here --
not anybody's opinion of it -- decides what it is allowed to do.

The one thing a simulator cannot check for you is whether the data was already
used to design the strategy. Measuring a model on the sample it was fitted to
will report an edge whether or not one exists, so ``Sample`` makes the
distinction explicit and :func:`calibration_from` refuses to certify an
in-sample run.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Sequence

from elyon.modules.strategy.domain import Calibration, ProbabilityTier, StrategyId
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec, quantize

from .trade import ExitReason, SimulatedTrade


class Sample(str, Enum):
    """Where the data came from, relative to how the strategy was built."""

    IN_SAMPLE = "IN_SAMPLE"          # designed on this data; proves nothing
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"  # held back; this is what counts
    FORWARD = "FORWARD"              # shadow-mode live data; the strongest


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """What a run of the simulator produced."""

    strategy: StrategyId
    dataset: str
    sample: Sample
    trades: tuple[SimulatedTrade, ...]
    data_hash: str
    config_hash: str
    registry_hash: str

    @property
    def count(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.won)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.r_multiple < ZERO)

    @property
    def win_rate(self) -> Decimal:
        if not self.trades:
            return ZERO
        return quantize(dec(self.wins) / dec(self.count), 4)

    @property
    def total_r(self) -> Decimal:
        return sum((t.r_multiple for t in self.trades), ZERO)

    @property
    def expectancy_r(self) -> Decimal:
        """Average R per trade -- the number that decides the tier."""
        if not self.trades:
            return ZERO
        return quantize(self.total_r / dec(self.count), 4)

    @property
    def average_win(self) -> Decimal:
        wins = [t.r_multiple for t in self.trades if t.won]
        return ZERO if not wins else quantize(sum(wins, ZERO) / dec(len(wins)), 4)

    @property
    def average_loss(self) -> Decimal:
        losses = [t.r_multiple for t in self.trades if t.r_multiple < ZERO]
        return (
            ZERO if not losses
            else quantize(sum(losses, ZERO) / dec(len(losses)), 4)
        )

    @property
    def profit_factor(self) -> Decimal | None:
        """Gross win over gross loss. ``None`` when nothing was lost yet.

        A run with no losing trades does not have an infinite profit factor; it
        has too few trades. Returning None says that instead of printing a
        number that would be quoted out of context.
        """
        gross_loss = -sum(
            (t.r_multiple for t in self.trades if t.r_multiple < ZERO), ZERO
        )
        if gross_loss == ZERO:
            return None
        gross_win = sum((t.r_multiple for t in self.trades if t.won), ZERO)
        return quantize(gross_win / gross_loss, 4)

    @property
    def expectancy_ex_best(self) -> Decimal:
        """Expectancy with the single best trade removed.

        The most useful sanity check in the report. A run whose edge collapses
        when one trade is taken out does not have an edge, it has an outlier,
        and outliers do not repeat on schedule. If this number is far below
        :attr:`expectancy_r`, the strategy has not been measured -- one lucky
        bar has.
        """
        if self.count < 2:
            return ZERO
        rs = sorted(t.r_multiple for t in self.trades)
        return quantize(sum(rs[:-1], ZERO) / dec(self.count - 1), 4)

    @property
    def carried_by_one_trade(self) -> bool:
        """Would this run stop looking profitable without its best trade?"""
        return self.expectancy_r > ZERO and self.expectancy_ex_best <= ZERO

    @property
    def equity_curve(self) -> tuple[Decimal, ...]:
        """Cumulative R after each trade, in order."""
        running = ZERO
        points = []
        for trade in self.trades:
            running += trade.r_multiple
            points.append(running)
        return tuple(points)

    @property
    def max_drawdown_r(self) -> Decimal:
        """Deepest peak-to-trough fall in R.

        The number that decides whether a strategy is survivable, as opposed to
        merely profitable. An edge you cannot sit through is not an edge you
        have.
        """
        peak = ZERO
        worst = ZERO
        for point in self.equity_curve:
            peak = max(peak, point)
            worst = min(worst, point - peak)
        return quantize(worst, 4)

    @property
    def longest_losing_streak(self) -> int:
        """What the run felt like at its worst, which is what gets systems switched off."""
        worst = streak = 0
        for trade in self.trades:
            streak = streak + 1 if not trade.won else 0
            worst = max(worst, streak)
        return worst

    def by_reason(self) -> dict[ExitReason, int]:
        counts = {reason: 0 for reason in ExitReason}
        for trade in self.trades:
            counts[trade.reason] += 1
        return {r: c for r, c in counts.items() if c}

    @property
    def unresolved(self) -> int:
        """Trades the sample ended in the middle of.

        Counted and reported rather than dropped: discarding them would bias
        the sample toward trades that happened to resolve.
        """
        return sum(1 for t in self.trades if t.reason is ExitReason.END_OF_DATA)

    def summary(self) -> str:
        pf = self.profit_factor
        return "\n".join([
            f"{self.strategy.value}  ·  {self.dataset}  ·  {self.sample.value}",
            f"  trades          {self.count}"
            + (f" ({self.unresolved} unresolved)" if self.unresolved else ""),
            f"  win rate        {self.win_rate}",
            f"  expectancy      {self.expectancy_r}R"
            + ("   ⚠ carried by one trade" if self.carried_by_one_trade else ""),
            f"  ex-best trade   {self.expectancy_ex_best}R",
            f"  total           {quantize(self.total_r, 4)}R",
            f"  avg win/loss    {self.average_win}R / {self.average_loss}R",
            f"  profit factor   {pf if pf is not None else 'n/a (no losses yet)'}",
            f"  max drawdown    {self.max_drawdown_r}R",
            f"  worst streak    {self.longest_losing_streak}",
            f"  exits           "
            + ", ".join(f"{r.value}×{c}" for r, c in self.by_reason().items()),
        ])


def calibration_from(report: BacktestReport) -> Calibration:
    """Convert a run into the evidence the tier system reads.

    Refuses an in-sample run. A strategy measured on the data it was designed
    on will show an edge whether or not one exists, and certifying that would
    quietly defeat the entire point of earning a tier.
    """
    if report.sample is Sample.IN_SAMPLE:
        raise DeterminismError(
            f"{report.strategy.value} was measured in-sample on "
            f"{report.dataset!r}; an in-sample result cannot certify a tier. "
            f"Hold data back and re-run, or mark the run OUT_OF_SAMPLE only "
            f"when it genuinely was."
        )

    return Calibration(
        sample_size=report.count,
        wins=report.wins,
        expectancy_r=report.expectancy_r,
        max_drawdown_r=report.max_drawdown_r,
        dataset=f"{report.dataset}:{report.data_hash[:12]}",
    )


def report_from(
    trades: Sequence[SimulatedTrade],
    *,
    strategy: StrategyId,
    dataset: str,
    sample: Sample,
    data_hash: str,
    config_hash: str,
    registry_hash: str,
) -> BacktestReport:
    return BacktestReport(
        strategy=strategy,
        dataset=dataset,
        sample=sample,
        trades=tuple(trades),
        data_hash=data_hash,
        config_hash=config_hash,
        registry_hash=registry_hash,
    )


def tier_of(report: BacktestReport) -> ProbabilityTier:
    """What this run would award, without certifying it.

    Useful for an in-sample look at whether a strategy is worth the cost of a
    proper out-of-sample run.
    """
    return Calibration(
        sample_size=report.count,
        wins=report.wins,
        expectancy_r=report.expectancy_r,
    ).tier
