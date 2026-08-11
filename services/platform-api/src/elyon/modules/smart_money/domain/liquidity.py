"""Liquidity pools, equal highs/lows and sweeps.

Implements D11-D16 of the Smart Money Engine Bible.

The premise the whole strategy rests on: price is drawn to resting orders. Stops
cluster above obvious highs and below obvious lows, and those clusters get taken
before the real move begins. A sweep -- penetrate, reject, close back inside --
is the fingerprint of that being done deliberately, and separating it from a
genuine breakout is the single most important call in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Final, Sequence

from elyon.modules.market_data.domain.model import Candle
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.shared_kernel.edcs.numeric import ZERO, dec
from .structure import Direction, Swing

DEFAULT_EQUAL_LEVEL_TOL: Final[str] = "0.10"      # x ATR
DEFAULT_EQUAL_MIN_TOUCHES: Final[int] = 2
DEFAULT_EQUAL_MIN_SEPARATION: Final[int] = 3       # bars
DEFAULT_SWEEP_MIN_PENETRATION: Final[str] = "0.05" # x ATR
DEFAULT_SWEEP_WICK_RATIO: Final[str] = "0.5"


class LiquidityType(str, Enum):
    """Which side of the book the resting orders sit on."""

    BSL = "BSL"  # buy-side: stops above highs
    SSL = "SSL"  # sell-side: stops below lows


class PoolState(str, Enum):
    INTACT = "INTACT"
    SWEPT = "SWEPT"


@dataclass(frozen=True, slots=True)
class LiquidityPool:
    """A price level where orders are expected to be resting."""

    level: Decimal
    type: LiquidityType
    origin: str
    touches: int
    state: PoolState = PoolState.INTACT
    indices: tuple[int, ...] = ()

    @property
    def strength(self) -> int:
        """More touches means a more obvious level, and a richer pool."""
        return self.touches


@dataclass(frozen=True, slots=True)
class EqualLevels:
    """D14/D15 -- two or more swings resting at effectively the same price."""

    level: Decimal
    type: LiquidityType
    touches: int
    indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Sweep:
    """D16 -- liquidity taken and rejected."""

    pool: LiquidityPool
    direction: Direction   # the bias the sweep implies, not the poke's direction
    penetration: Decimal
    index: int


def detect_equal_levels(
    swings: Sequence[Swing],
    atr: Decimal,
    *,
    is_high: bool,
    tolerance_atr: Decimal | None = None,
    min_touches: int = DEFAULT_EQUAL_MIN_TOUCHES,
    min_separation: int = DEFAULT_EQUAL_MIN_SEPARATION,
) -> list[EqualLevels]:
    """D14/D15 -- cluster swings that sit within tolerance of each other.

    Adjacent swings from the same oscillation are excluded: two touches of one
    wiggle are not a level that anyone has stacked orders behind.
    """
    tol = (tolerance_atr if tolerance_atr is not None else dec(DEFAULT_EQUAL_LEVEL_TOL)) * atr
    candidates = sorted(
        (s for s in swings if s.is_high == is_high), key=lambda s: (s.price, s.index)
    )

    clusters: list[list[Swing]] = []
    for swing in candidates:
        placed = False
        for cluster in clusters:
            if abs(swing.price - cluster[0].price) <= tol and all(
                abs(swing.index - other.index) >= min_separation for other in cluster
            ):
                cluster.append(swing)
                placed = True
                break
        if not placed:
            clusters.append([swing])

    pool_type = LiquidityType.BSL if is_high else LiquidityType.SSL
    out: list[EqualLevels] = []
    for cluster in clusters:
        if len(cluster) < min_touches:
            continue
        # Anchor at the extreme: that is where the stops actually sit.
        level = max(s.price for s in cluster) if is_high else min(s.price for s in cluster)
        out.append(
            EqualLevels(
                level=level,
                type=pool_type,
                touches=len(cluster),
                indices=tuple(sorted(s.index for s in cluster)),
            )
        )
    return sorted(out, key=lambda e: e.level)


def build_pools(
    swings: Sequence[Swing],
    atr: Decimal,
    *,
    tolerance_atr: Decimal | None = None,
) -> list[LiquidityPool]:
    """D11 -- turn swings and equal levels into a pool map.

    Equal levels are merged into a single, stronger pool rather than counted
    twice: three touches of one price is one magnet, not three.
    """
    tol = (tolerance_atr if tolerance_atr is not None else dec(DEFAULT_EQUAL_LEVEL_TOL)) * atr
    pools: list[LiquidityPool] = []

    for is_high in (True, False):
        pool_type = LiquidityType.BSL if is_high else LiquidityType.SSL
        equals = detect_equal_levels(swings, atr, is_high=is_high, tolerance_atr=tolerance_atr)
        claimed = {i for eq in equals for i in eq.indices}

        for eq in equals:
            pools.append(
                LiquidityPool(eq.level, pool_type, "equal", eq.touches, indices=eq.indices)
            )
        for swing in swings:
            if swing.is_high is not is_high or swing.index in claimed:
                continue
            if any(abs(swing.price - p.level) <= tol and p.type is pool_type for p in pools):
                continue
            pools.append(
                LiquidityPool(swing.price, pool_type, "swing", 1, indices=(swing.index,))
            )

    return sorted(pools, key=lambda p: (p.level, p.type.value))


def buy_side(pools: Sequence[LiquidityPool], price: Decimal) -> list[LiquidityPool]:
    """D12 -- intact BSL above price, nearest first."""
    above = [
        p for p in pools
        if p.type is LiquidityType.BSL and p.state is PoolState.INTACT and p.level > price
    ]
    return sorted(above, key=lambda p: (p.level - price, -p.strength))


def sell_side(pools: Sequence[LiquidityPool], price: Decimal) -> list[LiquidityPool]:
    """D13 -- intact SSL below price, nearest first."""
    below = [
        p for p in pools
        if p.type is LiquidityType.SSL and p.state is PoolState.INTACT and p.level < price
    ]
    return sorted(below, key=lambda p: (price - p.level, -p.strength))


def _wick_ratio(candle: Candle, *, upper: bool) -> Decimal:
    span = candle.high - candle.low
    if span == ZERO:
        return ZERO
    body_top = max(candle.open, candle.close)
    body_bottom = min(candle.open, candle.close)
    wick = candle.high - body_top if upper else body_bottom - candle.low
    return wick / span


def detect_sweeps(
    series: CandleSeries,
    pools: Sequence[LiquidityPool],
    index: int,
    atr: Decimal,
    *,
    min_penetration_atr: Decimal | None = None,
    wick_ratio: Decimal | None = None,
) -> list[Sweep]:
    """D16 -- pools taken and rejected on the bar at ``index``.

    Three conditions, all required: price pushed meaningfully past the level,
    it closed back on the original side, and the rejection wick dominates the
    bar. Closing *beyond* the level is the opposite event -- a breakout -- and
    must not be mistaken for a sweep.
    """
    min_pen = (
        min_penetration_atr if min_penetration_atr is not None
        else dec(DEFAULT_SWEEP_MIN_PENETRATION)
    ) * atr
    min_wick = wick_ratio if wick_ratio is not None else dec(DEFAULT_SWEEP_WICK_RATIO)

    candle = series[index]
    sweeps: list[Sweep] = []

    for pool in pools:
        if pool.state is not PoolState.INTACT:
            continue

        if pool.type is LiquidityType.BSL:
            penetrated = candle.high > pool.level + min_pen
            rejected = candle.close < pool.level
            dominant = _wick_ratio(candle, upper=True) >= min_wick
            # Taking buy-side stops flips the implication bearish.
            implied = Direction.DOWN
            penetration = candle.high - pool.level
        else:
            penetrated = candle.low < pool.level - min_pen
            rejected = candle.close > pool.level
            dominant = _wick_ratio(candle, upper=False) >= min_wick
            implied = Direction.UP
            penetration = pool.level - candle.low

        if penetrated and rejected and dominant:
            sweeps.append(
                Sweep(replace(pool, state=PoolState.SWEPT), implied, penetration, index)
            )

    return sweeps


def mark_swept(
    pools: Sequence[LiquidityPool], sweeps: Sequence[Sweep]
) -> list[LiquidityPool]:
    """Return the pool map with swept levels flagged."""
    consumed = {(s.pool.level, s.pool.type) for s in sweeps}
    return [
        replace(p, state=PoolState.SWEPT) if (p.level, p.type) in consumed else p
        for p in pools
    ]
