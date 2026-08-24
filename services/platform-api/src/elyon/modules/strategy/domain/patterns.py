"""Two patterns the ICT models need that the detector library does not yet have.

Both are compositions of existing primitives rather than new readings of the
tape, which is why they live here with the strategies instead of in the Smart
Money engine: they are opinions about how to combine detectors, and opinions
belong closer to the strategy than to the instrument.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.smart_money.domain.structure import Direction, detect_displacement
from elyon.modules.smart_money.domain.zones import (
    FairValueGap,
    PoiType,
    PointOfInterest,
    Zone,
    ZoneState,
    detect_fvg,
    detect_order_block,
)
from elyon.shared_kernel.edcs.numeric import ZERO, dec


def all_fvgs(
    series: CandleSeries, atr: Decimal, *, since: int = 0
) -> list[FairValueGap]:
    """Every unfilled gap in the window, oldest first."""
    found: list[FairValueGap] = []
    for i in range(max(since, 1), len(series) - 1):
        gap = detect_fvg(series, i, atr)
        if gap is not None:
            found.append(gap)
    return found


def unfilled(
    series: CandleSeries, gap: FairValueGap, *, as_of: int | None = None
) -> bool:
    """Has price left this gap alone since it formed?

    A gap that has already been traded back through is history. Entering on a
    filled gap is entering on a level that has already done its job.
    """
    end = len(series) - 1 if as_of is None else as_of
    for i in range(gap.zone.origin_index + 2, end + 1):
        candle = series[i]
        if candle.low <= gap.zone.low and candle.high >= gap.zone.high:
            return False
    return True


@dataclass(frozen=True, slots=True)
class BalancedPriceRange:
    """Two opposing gaps occupying the same prices.

    Price ran one way fast enough to leave a gap, then ran back the other way
    fast enough to leave another, and the two overlap. The overlap is a pocket
    that was traded twice and held properly neither time.
    """

    zone: Zone
    bullish: FairValueGap
    bearish: FairValueGap

    @property
    def direction(self) -> Direction:
        return self.zone.direction


def detect_bpr(
    series: CandleSeries, atr: Decimal, *, since: int = 0
) -> BalancedPriceRange | None:
    """The most recent balanced price range in the window."""
    gaps = all_fvgs(series, atr, since=since)
    best: BalancedPriceRange | None = None

    for i, first in enumerate(gaps):
        for second in gaps[i + 1:]:
            if first.direction is second.direction:
                continue
            low = max(first.zone.low, second.zone.low)
            high = min(first.zone.high, second.zone.high)
            if low >= high:
                continue

            # The later gap is the one still in play, so the pocket points the
            # way that gap points.
            later = second if second.zone.origin_index > first.zone.origin_index \
                else first
            bull = first if first.direction is Direction.UP else second
            bear = first if first.direction is Direction.DOWN else second
            zone = Zone(low, high, later.direction, later.zone.origin_index)
            if best is None or zone.origin_index > best.zone.origin_index:
                best = BalancedPriceRange(zone, bull, bear)

    return best


def detect_breaker(
    series: CandleSeries, atr: Decimal, direction: Direction, *, since: int = 0
) -> PointOfInterest | None:
    """An order block that failed, flipped, and now works the other way.

    The logic is the whole point of the pattern: a block is where one side was
    last willing to defend a price. When price closes clean through it, that
    side has been beaten, and the same band tends to reject from the other
    direction on the way back. A level that did not hold is not a level that
    stopped mattering.
    """
    # Find an impulse in ``direction``, then the block that stood against it.
    for end in range(len(series) - 1, since, -1):
        impulse = detect_displacement(series, end, atr)
        if impulse is None or impulse.direction is not direction:
            continue

        # The failed block is the opposite-direction block that preceded it.
        opposing = detect_order_block(
            series.upto(impulse.start_index),
            _mirror(impulse),
            has_fvg=False,
            had_prior_sweep=False,
        )
        if opposing is None:
            continue

        # It only counts if the impulse closed clean through the whole band.
        broke_through = (
            series[impulse.end_index].close > opposing.zone.high
            if direction is Direction.UP
            else series[impulse.end_index].close < opposing.zone.low
        )
        if not broke_through:
            continue

        flipped = Zone(
            opposing.zone.low,
            opposing.zone.high,
            direction,
            opposing.zone.origin_index,
            ZoneState.FRESH,
        )
        return PointOfInterest(PoiType.BREAKER, flipped, has_fvg=False,
                               had_prior_sweep=False)

    return None


def _mirror(displacement):
    """The same impulse read as if it pointed the other way.

    ``detect_order_block`` picks the last candle that traded *against* the move.
    To find the block that stood against this impulse we ask it for the block of
    the mirrored impulse -- same bars, opposite intent.
    """
    from dataclasses import replace

    return replace(displacement, direction=displacement.direction.opposite)


def overlaps(a: Zone, b: Zone) -> tuple[Decimal, Decimal] | None:
    """The prices two zones share, if any."""
    low, high = max(a.low, b.low), min(a.high, b.high)
    return (low, high) if low < high else None


def penetration_ratio(zone: Zone, price: Decimal) -> Decimal:
    """How deep into a zone price has travelled, 0 at the near edge.

    Measured from the edge price arrives at, which depends on direction: a
    bullish zone is entered from above.
    """
    span = zone.high - zone.low
    if span == ZERO:
        return ZERO
    if zone.direction is Direction.UP:
        depth = zone.high - price
    else:
        depth = price - zone.low
    ratio = depth / span
    return max(ZERO, min(dec("1"), ratio))
