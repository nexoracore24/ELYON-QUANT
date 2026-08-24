"""One simulated trade, from intent to exit.

Results are measured in **R** -- multiples of the risk taken -- rather than in
currency. A system that risks 0.5% and makes 2R is directly comparable to one
risking 2% on another instrument, and neither can flatter itself by trading
bigger. Currency P&L is a consequence of position sizing; R is a property of
the strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from elyon.modules.smart_money.domain.structure import Direction
from elyon.modules.strategy.domain import StrategyId
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec, quantize


class ExitReason(str, Enum):
    """How a trade ended. Each one means something different for research."""

    TARGET = "TARGET"              # the thesis played out
    STOP = "STOP"                  # the thesis was wrong
    GAP_THROUGH_STOP = "GAP_THROUGH_STOP"   # wrong, and worse than planned
    EXPIRED = "EXPIRED"            # took too long; capital freed
    END_OF_DATA = "END_OF_DATA"    # the sample ran out mid-trade


class FillModel(str, Enum):
    """How an entry is assumed to happen."""

    # Cross the spread on the next bar's open. Makes no assumption about
    # whether a resting order would have been filled.
    MARKET_NEXT_OPEN = "MARKET_NEXT_OPEN"
    # Rest an order in the strategy's entry zone and wait for price to come.
    # More faithful to how these models are actually traded, but it can only
    # ever be an assumption about queue position.
    LIMIT_INTO_ZONE = "LIMIT_INTO_ZONE"


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """A decision to trade, before anything has filled."""

    strategy: StrategyId
    direction: Direction
    signal_index: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    entry_zone: tuple[Decimal, Decimal] | None
    reason: str

    def __post_init__(self) -> None:
        # A stop on the wrong side is a guaranteed loss dressed as a trade, and
        # a backtest that accepts one will report a spectacular win rate.
        if self.direction is Direction.UP:
            if self.stop >= self.entry:
                raise DeterminismError(
                    f"long stop {self.stop} at or above entry {self.entry}"
                )
            if self.target <= self.entry:
                raise DeterminismError(
                    f"long target {self.target} at or below entry {self.entry}"
                )
        else:
            if self.stop <= self.entry:
                raise DeterminismError(
                    f"short stop {self.stop} at or below entry {self.entry}"
                )
            if self.target >= self.entry:
                raise DeterminismError(
                    f"short target {self.target} at or above entry {self.entry}"
                )

    @property
    def risk(self) -> Decimal:
        """Distance to the stop: the unit everything else is measured in."""
        return abs(self.entry - self.stop)

    @property
    def planned_r(self) -> Decimal:
        """Reward-to-risk as designed, before costs and before reality."""
        if self.risk == ZERO:
            raise DeterminismError("zero-risk intent")
        return abs(self.target - self.entry) / self.risk


@dataclass(frozen=True, slots=True)
class SimulatedTrade:
    """A trade that filled and closed."""

    strategy: StrategyId
    direction: Direction
    signal_index: int
    entry_index: int
    exit_index: int
    entry: Decimal          # the price actually paid, costs included
    stop: Decimal
    target: Decimal
    exit_price: Decimal     # the price actually received, costs included
    reason: ExitReason
    risk: Decimal

    @property
    def bars_held(self) -> int:
        return self.exit_index - self.entry_index

    @property
    def r_multiple(self) -> Decimal:
        """Result in multiples of the risk taken.

        Risk is measured from the *filled* entry to the stop, not from the
        intended entry: if slippage moved the fill, the trade really did risk
        more than planned, and pretending otherwise would understate the loss.
        """
        if self.risk == ZERO:
            return ZERO
        move = (self.exit_price - self.entry) * dec(int(self.direction.value))
        return quantize(move / self.risk, 4)

    @property
    def won(self) -> bool:
        return self.r_multiple > ZERO

    def __str__(self) -> str:
        return (
            f"{self.strategy.value} {self.direction.name} "
            f"bars {self.entry_index}→{self.exit_index} "
            f"{self.reason.value} {self.r_multiple:+}R"
        )
