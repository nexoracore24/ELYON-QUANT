"""Points of interest and price valuation.

Implements D18 (Fair Value Gap), D21 (Order Block), D25 (Dealing Range),
D26-D28 (Premium/Discount/Equilibrium), D29 (OTE) and D32 (Institutional
Fibonacci) of the Smart Money Engine Bible.

Two rules run through all of it. A zone is only interesting while it is
*unmitigated* -- once price has come back and taken what was there, the reason
to trade it is gone. And Fibonacci never fires a trade on its own: it is
anchored to real structure and only ever confirms a setup that already exists.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Final

from elyon.modules.market_data.domain.model import Candle
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec
from .structure import Direction, Displacement

DEFAULT_FVG_MIN_SIZE_ATR: Final[str] = "0.10"
DEFAULT_MITIGATION_THRESHOLD: Final[str] = "0.5"
OTE_LOW: Final[str] = "0.618"
OTE_OPTIMAL: Final[str] = "0.705"
OTE_HIGH: Final[str] = "0.786"
RETRACEMENT_LEVELS: Final[tuple[str, ...]] = ("0", "0.5", "0.618", "0.705", "0.786", "1")
PROJECTION_LEVELS: Final[tuple[str, ...]] = ("1.272", "1.618", "2.0", "2.618")


class ZoneState(str, Enum):
    """How much of a zone price has already consumed."""

    FRESH = "FRESH"
    TESTED = "TESTED"
    MITIGATED = "MITIGATED"
    INVALIDATED = "INVALIDATED"


class PoiType(str, Enum):
    ORDER_BLOCK = "ORDER_BLOCK"
    BREAKER = "BREAKER"
    MITIGATION = "MITIGATION"
    REJECTION = "REJECTION"


class Pricing(str, Enum):
    PREMIUM = "PREMIUM"
    DISCOUNT = "DISCOUNT"
    EQUILIBRIUM = "EQUILIBRIUM"


@dataclass(frozen=True, slots=True)
class Zone:
    """A price band with a direction and a lifecycle."""

    low: Decimal
    high: Decimal
    direction: Direction
    origin_index: int
    state: ZoneState = ZoneState.FRESH

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise DeterminismError(f"zone low {self.low} above high {self.high}")

    @property
    def mid(self) -> Decimal:
        """The 50% level -- the precision entry inside the zone."""
        return (self.low + self.high) / 2

    @property
    def size(self) -> Decimal:
        return self.high - self.low

    def contains(self, price: Decimal) -> bool:
        return self.low <= price <= self.high

    def touched_by(self, candle: Candle) -> bool:
        return candle.low <= self.high and candle.high >= self.low

    def penetration(self, candle: Candle) -> Decimal:
        """How deep into the zone price reached, as a fraction of its size."""
        if self.size == ZERO or not self.touched_by(candle):
            return ZERO
        if self.direction is Direction.UP:
            depth = self.high - max(candle.low, self.low)
        else:
            depth = min(candle.high, self.high) - self.low
        return depth / self.size

    def advance(
        self, candle: Candle, *, mitigation_threshold: Decimal | None = None
    ) -> Zone:
        """Update the zone's lifecycle against one candle.

        A close clean through the zone kills it outright; anything less is a
        test or a partial mitigation. States never move backwards -- that would
        be repainting.
        """
        if self.state is ZoneState.INVALIDATED:
            return self
        threshold = (
            mitigation_threshold if mitigation_threshold is not None
            else dec(DEFAULT_MITIGATION_THRESHOLD)
        )

        broken = (
            candle.close < self.low if self.direction is Direction.UP
            else candle.close > self.high
        )
        if broken:
            return replace(self, state=ZoneState.INVALIDATED)

        if not self.touched_by(candle):
            return self
        if self.penetration(candle) >= threshold:
            return replace(self, state=ZoneState.MITIGATED)
        return self if self.state is ZoneState.MITIGATED else replace(
            self, state=ZoneState.TESTED
        )


@dataclass(frozen=True, slots=True)
class FairValueGap:
    """D18 -- a three-bar imbalance price tends to come back and fill."""

    zone: Zone
    consequent_encroachment: Decimal  # the 50% level

    @property
    def direction(self) -> Direction:
        return self.zone.direction


def detect_fvg(
    series: CandleSeries,
    index: int,
    atr: Decimal,
    *,
    min_size_atr: Decimal | None = None,
    require_displacement: bool = True,
) -> FairValueGap | None:
    """D18 -- an unfilled gap between bar ``index-1`` and bar ``index+1``.

    The middle bar moved so hard that the wicks either side never overlapped,
    leaving prices that were never properly traded. Small gaps are filtered out;
    without a size floor every bit of noise becomes a "zone".
    """
    if index < 1 or index + 1 >= len(series):
        return None

    minimum = (
        min_size_atr if min_size_atr is not None else dec(DEFAULT_FVG_MIN_SIZE_ATR)
    ) * atr
    before, after = series[index - 1], series[index + 1]

    if after.low > before.high:
        low, high, direction = before.high, after.low, Direction.UP
    elif after.high < before.low:
        low, high, direction = after.high, before.low, Direction.DOWN
    else:
        return None

    if high - low < minimum:
        return None

    if require_displacement:
        middle = series[index]
        span = middle.high - middle.low
        if span == ZERO or abs(middle.close - middle.open) / span < dec("0.5"):
            return None

    zone = Zone(low, high, direction, index)
    return FairValueGap(zone, zone.mid)


@dataclass(frozen=True, slots=True)
class PointOfInterest:
    """D21 -- an order block or one of its relatives."""

    type: PoiType
    zone: Zone
    has_fvg: bool = False
    had_prior_sweep: bool = False

    @property
    def direction(self) -> Direction:
        return self.zone.direction

    @property
    def confidence(self) -> Decimal:
        """A coarse quality score: displacement alone is the floor.

        An unmitigated block that both left an imbalance behind and formed after
        liquidity was taken is telling a complete story; one that did neither is
        just the last opposite candle before a move.
        """
        score = dec("0.5")
        if self.has_fvg:
            score += dec("0.2")
        if self.had_prior_sweep:
            score += dec("0.2")
        if self.zone.state is ZoneState.FRESH:
            score += dec("0.1")
        return score


def detect_order_block(
    series: CandleSeries,
    displacement: Displacement,
    *,
    use_full_range: bool = True,
    has_fvg: bool = False,
    had_prior_sweep: bool = False,
) -> PointOfInterest | None:
    """D21 -- the last opposite candle before an impulsive move.

    That candle is where the move was loaded: the last time the other side was
    willing to trade before price left. Scanning backwards from the impulse, the
    *nearest* such candle is the block.
    """
    wants_bearish_candle = displacement.direction is Direction.UP

    for i in range(displacement.start_index, -1, -1):
        candle = series[i]
        is_bearish = candle.close < candle.open
        is_bullish = candle.close > candle.open
        if (wants_bearish_candle and is_bearish) or (not wants_bearish_candle and is_bullish):
            low = candle.low if use_full_range else min(candle.open, candle.close)
            high = candle.high if use_full_range else max(candle.open, candle.close)
            zone = Zone(low, high, displacement.direction, i)
            return PointOfInterest(PoiType.ORDER_BLOCK, zone, has_fvg, had_prior_sweep)

    return None


@dataclass(frozen=True, slots=True)
class DealingRange:
    """D25 -- the impulsive leg everything else is measured against."""

    low: Decimal
    high: Decimal
    direction: Direction
    origin_index: int
    terminal_index: int

    def __post_init__(self) -> None:
        if self.high <= self.low:
            raise DeterminismError("dealing range must have positive size")

    @property
    def span(self) -> Decimal:
        return self.high - self.low

    @property
    def equilibrium(self) -> Decimal:
        return (self.low + self.high) / 2

    def position_of(self, price: Decimal) -> Decimal:
        """Where a price sits in the range: 0 at the low, 1 at the high."""
        return (price - self.low) / self.span

    def classify(self, price: Decimal, *, band: Decimal | None = None) -> Pricing:
        """D26-D28 -- premium, discount or the neutral band between them.

        The point of this is to stop the engine buying at the top of its own
        range: cheap for longs, expensive for shorts, nothing in the middle.
        """
        edge = band if band is not None else dec("0.05")
        pos = self.position_of(price)
        if pos > dec("0.5") + edge:
            return Pricing.PREMIUM
        if pos < dec("0.5") - edge:
            return Pricing.DISCOUNT
        return Pricing.EQUILIBRIUM


@dataclass(frozen=True, slots=True)
class Fibonacci:
    """D32 -- levels anchored to a real impulsive leg.

    Retracements are measured back from the destination, projections forward
    from the origin. Fixing that convention is what stops the levels shifting
    depending on who drew them.
    """

    origin: Decimal
    destination: Decimal
    retracements: dict[str, Decimal]
    projections: dict[str, Decimal]
    ote_low: Decimal
    ote_high: Decimal
    ote_optimal: Decimal

    @property
    def span(self) -> Decimal:
        return self.destination - self.origin

    def in_ote(self, price: Decimal) -> bool:
        return self.ote_low <= price <= self.ote_high


def compute_fibonacci(
    origin: Decimal,
    destination: Decimal,
    *,
    min_span: Decimal | None = None,
) -> Fibonacci | None:
    """D32 -- draw the levels for a leg, or refuse if the leg is too small.

    A leg with no span has no levels; one that barely moved produces an OTE band
    a few ticks wide, which is not something to trade.
    """
    span = destination - origin
    if span == ZERO:
        return None
    if min_span is not None and abs(span) < min_span:
        return None

    retracements = {level: destination - dec(level) * span for level in RETRACEMENT_LEVELS}
    projections = {level: origin + dec(level) * span for level in PROJECTION_LEVELS}

    bound_a = destination - dec(OTE_LOW) * span
    bound_b = destination - dec(OTE_HIGH) * span
    return Fibonacci(
        origin=origin,
        destination=destination,
        retracements=retracements,
        projections=projections,
        ote_low=min(bound_a, bound_b),
        ote_high=max(bound_a, bound_b),
        ote_optimal=destination - dec(OTE_OPTIMAL) * span,
    )


def fibonacci_for(dealing_range: DealingRange) -> Fibonacci | None:
    """Draw Fibonacci over a dealing range, respecting the leg's direction."""
    if dealing_range.direction is Direction.UP:
        return compute_fibonacci(dealing_range.low, dealing_range.high)
    return compute_fibonacci(dealing_range.high, dealing_range.low)
