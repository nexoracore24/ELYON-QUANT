"""Classifying what kind of market this is.

Two independent readings, deliberately kept apart because they answer different
questions and a system that conflates them will trade a violent range as though
it were a trend:

*   **Volatility** -- is anything happening at all, and is it governable?
*   **Regime** -- is the movement going somewhere, or churning?

A dead market and a violent one both fail, for opposite reasons: one offers no
move worth capturing, the other moves so much that any sane stop is noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from elyon.modules.market_data.domain.atr import efficiency_ratio
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.smart_money.domain.structure import Trend, build_structure
from elyon.shared_kernel.edcs.numeric import ZERO, dec, quantize

from .dna import MarketDna

# Kaufman efficiency thresholds. Below the floor price is retracing almost every
# step it takes; above the ceiling it is travelling in something close to a line.
CHURN_CEILING = dec("0.25")
TREND_FLOOR = dec("0.45")

# How much wider than its own recent past a range has to get to count as
# expanding, or narrower to count as compressing.
EXPANSION_RATIO = dec("1.35")
COMPRESSION_RATIO = dec("0.70")


class VolatilityRegime(str, Enum):
    """How much this instrument is moving, relative to its own normal."""

    DEAD = "DEAD"          # nothing to capture
    QUIET = "QUIET"        # thin, but tradeable
    NORMAL = "NORMAL"
    ACTIVE = "ACTIVE"      # the good one
    EXTREME = "EXTREME"    # ungovernable; any sane stop is noise

    @property
    def is_tradeable(self) -> bool:
        return self in (VolatilityRegime.QUIET, VolatilityRegime.NORMAL,
                        VolatilityRegime.ACTIVE)

    @property
    def is_ideal(self) -> bool:
        return self in (VolatilityRegime.NORMAL, VolatilityRegime.ACTIVE)


class MarketRegime(str, Enum):
    """What the movement is doing."""

    TREND = "TREND"                # going somewhere
    RANGE = "RANGE"                # bounded, but with clean edges
    EXPANSION = "EXPANSION"        # breaking out of a range
    COMPRESSION = "COMPRESSION"    # coiling; the move has not started
    CHURN = "CHURN"                # movement without direction -- the trap
    UNDETERMINED = "UNDETERMINED"  # not enough to say

    @property
    def is_tradeable(self) -> bool:
        """Churn is where accounts go to die: it looks like activity."""
        return self in (MarketRegime.TREND, MarketRegime.RANGE,
                        MarketRegime.EXPANSION)


@dataclass(frozen=True, slots=True)
class RegimeReading:
    """The full regime picture, with the numbers behind it."""

    volatility: VolatilityRegime
    regime: MarketRegime
    atr_ratio: Decimal          # ATR as a multiple of the instrument's normal
    efficiency: Decimal         # Kaufman ER over the window
    range_ratio: Decimal        # current range vs the preceding stretch
    trend: Trend
    detail: str

    @property
    def is_tradeable(self) -> bool:
        return self.volatility.is_tradeable and self.regime.is_tradeable


def classify_volatility(atr: Decimal, dna: MarketDna) -> VolatilityRegime:
    """Where this ATR sits in the instrument's own distribution.

    Expressed in DNA multiples, never in prices -- an ATR of 0.0008 is dead in
    gold and ordinary in EURUSD, and a single absolute threshold would be wrong
    for every instrument but one.
    """
    ratio = dna.atr_ratio(atr)
    bands = dna.bands
    if ratio < bands.dead:
        return VolatilityRegime.DEAD
    if ratio < bands.low:
        return VolatilityRegime.QUIET
    if ratio < bands.high:
        return VolatilityRegime.NORMAL
    if ratio < bands.extreme:
        return VolatilityRegime.ACTIVE
    return VolatilityRegime.EXTREME


def classify_regime(
    series: CandleSeries, *, swing_grade: int = 1, window: int = 20
) -> tuple[MarketRegime, Decimal, Decimal, Trend]:
    """Read the regime from efficiency and structure together.

    Neither alone is enough. Efficiency says whether price is travelling or
    retracing; structure says whether the travel has direction. A market can be
    efficient and still be a one-off spike, and it can be structurally bullish
    while churning sideways.
    """
    if len(series) < max(4, window // 2):
        return MarketRegime.UNDETERMINED, ZERO, ZERO, Trend.UNDETERMINED

    recent = list(series)[-window:]
    closes = [c.close for c in recent]
    efficiency = efficiency_ratio(closes)

    span = max(c.high for c in recent) - min(c.low for c in recent)
    prior = list(series)[-window * 2 : -window]
    if prior:
        prior_span = max(c.high for c in prior) - min(c.low for c in prior)
        range_ratio = (
            quantize(span / prior_span, 4) if prior_span > ZERO else ZERO
        )
    else:
        range_ratio = dec("1")

    trend = build_structure(series, grade=swing_grade).trend
    directional = trend in (Trend.BULLISH, Trend.BEARISH)

    if efficiency >= TREND_FLOOR and directional:
        regime = MarketRegime.TREND
    elif range_ratio >= EXPANSION_RATIO and efficiency > CHURN_CEILING:
        regime = MarketRegime.EXPANSION
    elif range_ratio <= COMPRESSION_RATIO:
        regime = MarketRegime.COMPRESSION
    elif efficiency <= CHURN_CEILING:
        # Movement without direction. It looks like opportunity and is not.
        regime = MarketRegime.CHURN
    else:
        regime = MarketRegime.RANGE

    return regime, efficiency, range_ratio, trend


def read_regime(
    series: CandleSeries,
    atr: Decimal,
    dna: MarketDna,
    *,
    swing_grade: int = 1,
    window: int = 20,
) -> RegimeReading:
    """Both readings at once, with an explanation attached."""
    volatility = classify_volatility(atr, dna)
    regime, efficiency, range_ratio, trend = classify_regime(
        series, swing_grade=swing_grade, window=window
    )
    ratio = dna.atr_ratio(atr)

    detail = (
        f"{volatility.value} volatility ({ratio}× typical), "
        f"{regime.value} (ER {efficiency}, range {range_ratio}× prior, "
        f"structure {trend.value})"
    )
    return RegimeReading(
        volatility=volatility,
        regime=regime,
        atr_ratio=ratio,
        efficiency=efficiency,
        range_ratio=range_ratio,
        trend=trend,
        detail=detail,
    )
