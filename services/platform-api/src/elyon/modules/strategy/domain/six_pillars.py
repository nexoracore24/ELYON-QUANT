"""The six-pillar strategy.

ELYON QUANT trades one thesis: price is drawn to liquidity, takes it, and then
travels from an institutional zone in the direction of the higher-timeframe
trend. Six things have to line up for that story to be true, and this module
locates all six in one pass so the strategy is a single object rather than a
habit spread across modules.

    1. TENDENCIA    -- which way is the market actually going?
    2. LIQUIDEZ     -- where are the resting orders, and were they taken?
    3. ORDER BLOCK  -- where did the move originate?
    4. FVG          -- did it leave an imbalance behind?
    5. FIBONACCI    -- is the pullback measured against a real leg?
    6. ZONA OTE     -- is price at a good price within that leg?

Each pillar reports whether it was found and, when it was not, exactly what was
missing -- because "no trade" needs a reason as precise as "trade".
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final

from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.smart_money.domain.liquidity import (
    LiquidityPool,
    Sweep,
    build_pools,
    buy_side,
    detect_sweeps,
    sell_side,
)
from elyon.modules.smart_money.domain.structure import (
    Direction,
    Displacement,
    Structure,
    Trend,
    build_structure,
    detect_displacement,
    detect_swings,
)
from elyon.modules.smart_money.domain.zones import (
    DealingRange,
    FairValueGap,
    Fibonacci,
    PointOfInterest,
    Pricing,
    compute_fibonacci,
    detect_fvg,
    detect_order_block,
)
DEFAULT_SWING_GRADE: Final[int] = 1


class Pillar(str, Enum):
    """The six things the strategy looks for, in the order it looks for them."""

    TENDENCIA = "TENDENCIA"
    LIQUIDEZ = "LIQUIDEZ"
    ORDER_BLOCK = "ORDER_BLOCK"
    FVG = "FVG"
    FIBONACCI = "FIBONACCI"
    OTE = "OTE"


@dataclass(frozen=True, slots=True)
class PillarFinding:
    """Whether one pillar stands, and what it rests on."""

    pillar: Pillar
    found: bool
    detail: str

    def __str__(self) -> str:
        return f"{'✓' if self.found else '·'} {self.pillar.value}: {self.detail}"


@dataclass(frozen=True, slots=True)
class SixPillarSetup:
    """The full read of a market at one moment.

    Holds both the verdict and the evidence, so the caller can score it, explain
    it, or place a trade from it without re-deriving anything.
    """

    symbol: str
    bar_index: int
    direction: Direction | None
    findings: tuple[PillarFinding, ...]

    # The evidence behind each pillar, for sizing and explanation.
    structure: Structure
    trend: Trend
    pools: tuple[LiquidityPool, ...]
    sweeps: tuple[Sweep, ...]
    displacement: Displacement | None
    order_block: PointOfInterest | None
    fvg: FairValueGap | None
    dealing_range: DealingRange | None
    fibonacci: Fibonacci | None
    pricing: Pricing | None
    price: Decimal

    @property
    def found(self) -> tuple[Pillar, ...]:
        return tuple(f.pillar for f in self.findings if f.found)

    @property
    def missing(self) -> tuple[Pillar, ...]:
        return tuple(f.pillar for f in self.findings if not f.found)

    @property
    def pillars_found(self) -> int:
        return len(self.found)

    @property
    def complete(self) -> bool:
        """All six aligned -- the setup the strategy was written for."""
        return self.pillars_found == len(Pillar)

    def finding(self, pillar: Pillar) -> PillarFinding:
        return next(f for f in self.findings if f.pillar is pillar)

    @property
    def favourable_pricing(self) -> bool:
        """Buying in discount, selling in premium -- never the other way round.

        Equilibrium counts as neither: paying the midpoint of a leg is not an
        edge, it is a coin flip with spread on top.
        """
        if self.pricing is None or self.direction is None:
            return False
        if self.direction is Direction.UP:
            return self.pricing is Pricing.DISCOUNT
        return self.pricing is Pricing.PREMIUM

    @property
    def entry_zone(self) -> tuple[Decimal, Decimal] | None:
        """Where to enter: the overlap of the block and the OTE band.

        Taking the intersection rather than either alone is the point of
        confluence -- it is the price that is both institutionally significant
        and well-valued, which is a smaller and better place than either.
        """
        if self.order_block is None:
            return None
        low, high = self.order_block.zone.low, self.order_block.zone.high
        if self.fibonacci is not None:
            low = max(low, self.fibonacci.ote_low)
            high = min(high, self.fibonacci.ote_high)
            if low > high:  # they do not overlap; fall back to the block
                return (self.order_block.zone.low, self.order_block.zone.high)
        return (low, high)

    @property
    def invalidation(self) -> Decimal | None:
        """Where the thesis is wrong -- beyond the sweep, or beyond the block.

        The stop belongs past the liquidity that was taken: if price goes back
        through it, the move that was supposed to follow the sweep never came.
        Which side is "past" depends on the direction, so a short invalidates
        above and a long below -- never the same number for both.
        """
        long_side = self.direction is not Direction.DOWN
        if self.sweeps:
            levels = [s.pool.level for s in self.sweeps]
            return min(levels) if long_side else max(levels)
        if self.order_block is not None:
            zone = self.order_block.zone
            return zone.low if long_side else zone.high
        return None

    def stop_loss(self, buffer: Decimal) -> Decimal | None:
        """The invalidation, widened by ``buffer``.

        A buffer must always widen the stop, which means it moves *down* on a
        long and *up* on a short. Subtracting on both -- the obvious mistake --
        tightens a short into the noise it was meant to survive, so the sign
        lives here rather than in every caller.
        """
        if self.invalidation is None:
            return None
        if buffer < 0:
            raise ValueError("stop buffer must not be negative")
        if self.direction is Direction.DOWN:
            return self.invalidation + buffer
        return self.invalidation - buffer

    @property
    def target(self) -> Decimal | None:
        """The liquidity the move is reaching for."""
        if not self.pools:
            return None
        if self.direction is Direction.UP:
            above = buy_side(list(self.pools), self.price)
            return max((p.level for p in above), default=None)
        below = sell_side(list(self.pools), self.price)
        return min((p.level for p in below), default=None)


def locate_six_pillars(
    series: CandleSeries,
    atr: Decimal,
    *,
    symbol: str = "",
    swing_grade: int = DEFAULT_SWING_GRADE,
) -> SixPillarSetup:
    """Look for all six pillars in one pass over the confirmed series.

    Order matters: trend first, because it decides which side to look for, and
    the rest is only meaningful relative to it.
    """
    bar_index = len(series) - 1
    price = series[-1].close
    findings: list[PillarFinding] = []

    # 1. TENDENCIA --------------------------------------------------------
    swings = detect_swings(series, grade=swing_grade)
    structure = build_structure(series, grade=swing_grade)

    # 2. LIQUIDEZ ---------------------------------------------------------
    pools = build_pools(swings, atr)
    sweeps: list[Sweep] = []
    for i in range(len(series)):
        sweeps.extend(detect_sweeps(series, pools, i, atr))

    # A sweep prints a lower low (or higher high) on purpose. Reading trend
    # from bars that include it would mistake the manipulation for a reversal,
    # so the bias comes from the structure before the most recent sweep.
    key_sweep = max(sweeps, key=lambda s: s.index) if sweeps else None
    if key_sweep is not None and key_sweep.index > swing_grade * 2 + 1:
        bias_structure = build_structure(
            series.upto(key_sweep.index - 1), grade=swing_grade
        )
    else:
        bias_structure = structure
    trend = bias_structure.trend

    # 3. Direction --------------------------------------------------------
    direction: Direction | None = None
    if trend is Trend.BULLISH:
        direction = Direction.UP
    elif trend is Trend.BEARISH:
        direction = Direction.DOWN
    elif key_sweep is not None:
        # No settled trend, but liquidity was taken: the sweep implies a side.
        direction = key_sweep.direction

    findings.append(
        PillarFinding(
            Pillar.TENDENCIA,
            trend in (Trend.BULLISH, Trend.BEARISH),
            f"{trend.value}" + (
                " (read before the sweep)" if key_sweep is not None else ""
            ),
        )
    )

    relevant_sweeps = tuple(
        s for s in sweeps if direction is None or s.direction is direction
    )
    findings.append(
        PillarFinding(
            Pillar.LIQUIDEZ,
            bool(relevant_sweeps),
            f"{len(relevant_sweeps)} sweep(s) in direction"
            if relevant_sweeps
            else f"{len(pools)} pools mapped, none swept in direction",
        )
    )

    # 4. ORDER BLOCK + FVG ------------------------------------------------
    displacement = _latest_displacement(series, atr, direction)
    order_block: PointOfInterest | None = None
    fvg: FairValueGap | None = None

    if displacement is not None:
        fvg = detect_fvg(series, displacement.end_index - 1, atr)
        order_block = detect_order_block(
            series,
            displacement,
            has_fvg=fvg is not None,
            had_prior_sweep=bool(relevant_sweeps),
        )

    findings.append(
        PillarFinding(
            Pillar.ORDER_BLOCK,
            order_block is not None,
            f"[{order_block.zone.low}, {order_block.zone.high}] "
            f"{order_block.zone.state.value}, confidence {order_block.confidence}"
            if order_block is not None
            else "no displacement to anchor a block",
        )
    )
    findings.append(
        PillarFinding(
            Pillar.FVG,
            fvg is not None,
            f"[{fvg.zone.low}, {fvg.zone.high}] CE {fvg.consequent_encroachment}"
            if fvg is not None
            else "no imbalance left behind",
        )
    )

    # 5. FIBONACCI + 6. OTE ------------------------------------------------
    dealing_range, fibonacci, pricing = _measure_leg(series, displacement, direction)

    findings.append(
        PillarFinding(
            Pillar.FIBONACCI,
            fibonacci is not None,
            f"leg {fibonacci.origin} → {fibonacci.destination}"
            if fibonacci is not None
            else "no impulsive leg to measure",
        )
    )

    in_ote = fibonacci is not None and fibonacci.in_ote(price)
    findings.append(
        PillarFinding(
            Pillar.OTE,
            in_ote,
            f"price {price} inside [{fibonacci.ote_low}, {fibonacci.ote_high}]"
            if in_ote and fibonacci is not None
            else (
                f"price {price} outside [{fibonacci.ote_low}, {fibonacci.ote_high}]"
                if fibonacci is not None
                else "no Fibonacci to place price against"
            ),
        )
    )

    return SixPillarSetup(
        symbol=symbol,
        bar_index=bar_index,
        direction=direction,
        findings=tuple(findings),
        structure=structure,
        trend=trend,
        pools=tuple(pools),
        sweeps=tuple(relevant_sweeps),
        displacement=displacement,
        order_block=order_block,
        fvg=fvg,
        dealing_range=dealing_range,
        fibonacci=fibonacci,
        pricing=pricing,
        price=price,
    )


def _latest_displacement(
    series: CandleSeries, atr: Decimal, direction: Direction | None
) -> Displacement | None:
    """The most recent impulse, preferring one that agrees with the bias."""
    fallback: Displacement | None = None
    for i in range(len(series) - 1, 0, -1):
        found = detect_displacement(series, i, atr)
        if found is None:
            continue
        if direction is None or found.direction is direction:
            return found
        fallback = fallback or found
    return fallback


def _measure_leg(
    series: CandleSeries,
    displacement: Displacement | None,
    direction: Direction | None,
) -> tuple[DealingRange | None, Fibonacci | None, Pricing | None]:
    """Anchor Fibonacci to the impulsive leg, not to the whole window.

    Premium and discount only mean something relative to the move in play; a
    range drawn over arbitrary bars would put price wherever the window happens
    to start.
    """
    if displacement is None:
        return None, None, None

    leg = [series[i] for i in range(displacement.start_index, len(series))]
    low = min(c.low for c in leg)
    high = max(c.high for c in leg)
    if high <= low:
        return None, None, None

    leg_direction = direction or displacement.direction
    dealing_range = DealingRange(
        low, high, leg_direction, displacement.start_index, len(series) - 1
    )

    if leg_direction is Direction.UP:
        fibonacci = compute_fibonacci(low, high)
    else:
        fibonacci = compute_fibonacci(high, low)

    return dealing_range, fibonacci, dealing_range.classify(series[-1].close)
