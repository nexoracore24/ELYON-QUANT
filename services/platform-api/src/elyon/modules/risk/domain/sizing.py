"""Position sizing.

The size follows from the stop, never the other way round. Deciding "I'll trade
one lot" and then finding somewhere to put the stop is how accounts die; here
the stop defines the risk, and the risk defines the size.

Money is Decimal throughout and lots round *down* onto the broker's step, so a
rounding artefact can never put more at risk than was approved.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from elyon.shared_kernel.edcs.numeric import (
    ZERO,
    DeterminismError,
    dec,
    quantize_down,
)


class SizingRejection(str, Enum):
    """Why a trade could not be sized. Each maps to a hard rule."""

    INVALID_STOP = "INVALID_STOP"
    STOP_TOO_WIDE = "STOP_TOO_WIDE"
    RR_BELOW_MINIMUM = "RR_BELOW_MINIMUM"
    BELOW_MIN_LOT = "BELOW_MIN_LOT"


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """What the broker will accept, and what a price move is worth."""

    lot_step: Decimal
    min_lot: Decimal
    max_lot: Decimal
    value_per_price_unit: Decimal  # account currency per 1.0 of price, per lot

    def __post_init__(self) -> None:
        if self.lot_step <= ZERO:
            raise DeterminismError("lot_step must be positive")
        if self.min_lot > self.max_lot:
            raise DeterminismError("min_lot cannot exceed max_lot")
        if self.value_per_price_unit <= ZERO:
            raise DeterminismError("value_per_price_unit must be positive")


@dataclass(frozen=True, slots=True)
class SizingRequest:
    equity: Decimal
    risk_fraction: Decimal      # e.g. 0.005 for 0.5%
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None
    spec: InstrumentSpec


@dataclass(frozen=True, slots=True)
class SizingResult:
    approved: bool
    lots: Decimal = ZERO
    risk_amount: Decimal = ZERO
    reward_risk: Decimal | None = None
    rejection: SizingRejection | None = None

    @property
    def rejected(self) -> bool:
        return not self.approved


def reward_to_risk(entry: Decimal, stop_loss: Decimal, take_profit: Decimal) -> Decimal:
    """How many units of reward are on offer per unit of risk."""
    risk = abs(entry - stop_loss)
    if risk == ZERO:
        raise DeterminismError("cannot compute R:R against a zero stop distance")
    return abs(take_profit - entry) / risk


def size_position(
    request: SizingRequest,
    *,
    min_reward_risk: Decimal | None = None,
    max_stop_atr: Decimal | None = None,
    atr: Decimal | None = None,
) -> SizingResult:
    """Derive lot size from the stop distance, or reject with a reason.

    The checks run cheapest-first and the first failure wins, so a rejection
    always names the single rule that actually stopped the trade.
    """
    stop_distance = abs(request.entry - request.stop_loss)
    if stop_distance == ZERO:
        return SizingResult(approved=False, rejection=SizingRejection.INVALID_STOP)

    if max_stop_atr is not None and atr is not None and atr > ZERO:
        if stop_distance > max_stop_atr * atr:
            # A stop this wide either misreads the structure or ruins the R:R.
            return SizingResult(approved=False, rejection=SizingRejection.STOP_TOO_WIDE)

    rr: Decimal | None = None
    if request.take_profit is not None:
        rr = reward_to_risk(request.entry, request.stop_loss, request.take_profit)
        if min_reward_risk is not None and rr < min_reward_risk:
            return SizingResult(
                approved=False,
                reward_risk=rr,
                rejection=SizingRejection.RR_BELOW_MINIMUM,
            )

    budget = request.equity * request.risk_fraction
    risk_per_lot = stop_distance * request.spec.value_per_price_unit
    raw_lots = budget / risk_per_lot

    lots = quantize_down(raw_lots, request.spec.lot_step)
    if lots > request.spec.max_lot:
        lots = quantize_down(request.spec.max_lot, request.spec.lot_step)

    if lots < request.spec.min_lot or lots <= ZERO:
        # Too small to trade: the honest answer is no trade, not a rounded-up one.
        return SizingResult(
            approved=False, reward_risk=rr, rejection=SizingRejection.BELOW_MIN_LOT
        )

    return SizingResult(
        approved=True,
        lots=lots,
        risk_amount=lots * risk_per_lot,
        reward_risk=rr,
    )


def scale_risk(
    base_fraction: Decimal,
    *,
    multipliers: list[Decimal],
    floor: Decimal,
    ceiling: Decimal,
) -> Decimal:
    """Apply dynamic risk multipliers, hard-capped at both ends.

    Context and conviction may lean the size up or down, but never past the
    ceiling: no combination of favourable signals can talk the engine into
    risking more than the profile allows.
    """
    if floor > ceiling:
        raise DeterminismError("risk floor cannot exceed ceiling")
    scaled = base_fraction
    for multiplier in multipliers:
        if multiplier < ZERO:
            raise DeterminismError(f"risk multiplier cannot be negative: {multiplier}")
        scaled = scaled * multiplier
    return min(max(scaled, floor), ceiling)
