"""Market Context Engine domain.

The first engine in the pipeline. Before the Smart Money engine looks for a
single order block, this one decides whether the market is worth looking in at
all, and emits a Context Score of 0-100 with the reason attached. If the gate
fails, nothing downstream runs -- and *why nothing ran* is recorded just as
carefully as why a trade was taken.

Market DNA supplies the per-instrument thresholds every reading is expressed in.
Its inviolable rule: **DNA adapts filters, never rules.**
"""

from .context import (
    CONTEXT_WEIGHTS,
    DEFAULT_HYSTERESIS,
    DEFAULT_THRESHOLD,
    ContextBand,
    ContextConfig,
    ContextFactor,
    ContextVeto,
    FactorReading,
    GateResult,
    MarketContext,
    NewsCalendar,
    NoCalendar,
    read_context,
)
from .dna import (
    ENGINE_DEFAULTS,
    MIN_DNA_SAMPLE,
    REFERENCE_PROFILES,
    AssetClass,
    MarketDna,
    Provenance,
    VolatilityBands,
    learn_dna,
    profile_for,
)
from .regime import (
    MarketRegime,
    RegimeReading,
    VolatilityRegime,
    classify_regime,
    classify_volatility,
    read_regime,
)

__all__ = [
    "AssetClass", "CONTEXT_WEIGHTS", "ContextBand", "ContextConfig",
    "ContextFactor", "ContextVeto", "DEFAULT_HYSTERESIS", "DEFAULT_THRESHOLD",
    "ENGINE_DEFAULTS", "FactorReading", "GateResult", "MIN_DNA_SAMPLE",
    "MarketContext", "MarketDna", "MarketRegime", "NewsCalendar", "NoCalendar",
    "Provenance", "REFERENCE_PROFILES", "RegimeReading", "VolatilityBands",
    "VolatilityRegime", "classify_regime", "classify_volatility", "learn_dna",
    "profile_for", "read_context", "read_regime",
]
