"""Trading costs.

Expectancy computed without costs is fiction. The spread alone turns a great
many "profitable" systems into losing ones, and a backtest that omits it is not
optimistic -- it is measuring a different system than the one that would trade.

Costs are expressed in price units and applied at both ends of a trade, so a
round turn pays twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from elyon.modules.smart_money.domain.structure import Direction
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec


@dataclass(frozen=True, slots=True)
class CostModel:
    """What it costs to get in and out.

    ``spread`` is the full bid/ask distance; a buyer pays half of it on entry
    and half again on exit, which is the same as paying the whole spread once
    per round turn. Modelling it as a single hit at entry would flatter every
    short-hold strategy.
    """

    spread: Decimal = ZERO
    commission_per_unit: Decimal = ZERO
    slippage: Decimal = ZERO

    def __post_init__(self) -> None:
        for name in ("spread", "commission_per_unit", "slippage"):
            if getattr(self, name) < ZERO:
                raise DeterminismError(f"{name} cannot be negative")

    @property
    def round_turn(self) -> Decimal:
        """Total cost of a complete trade, in price units."""
        return self.spread + self.slippage * dec(2) + self.commission_per_unit * dec(2)

    def entry_price(self, ideal: Decimal, direction: Direction) -> Decimal:
        """What you actually pay, which is never the price you saw.

        Costs always move against the trade: a buyer fills higher, a seller
        lower. Applying them symmetrically would let a backtest occasionally
        profit from its own friction.
        """
        penalty = self.spread / dec(2) + self.slippage
        return ideal + penalty if direction is Direction.UP else ideal - penalty

    def exit_price(self, ideal: Decimal, direction: Direction) -> Decimal:
        """What you actually receive."""
        penalty = self.spread / dec(2) + self.slippage
        return ideal - penalty if direction is Direction.UP else ideal + penalty


# A retail-ish EURUSD default: 1 pip spread, no commission, half a pip of
# slippage. Deliberately not zero -- a zero-cost default is how a backtest
# quietly becomes a sales pitch.
DEFAULT_COSTS = CostModel(
    spread=dec("0.00010"), slippage=dec("0.00005"), commission_per_unit=ZERO
)

FREE = CostModel()
