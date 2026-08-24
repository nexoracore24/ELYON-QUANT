"""Strategies: the house model, the ICT catalog, and how they combine.

The six-pillar model (TENDENCIA · LIQUIDEZ · ORDER BLOCK · FVG · FIBONACCI ·
ZONA OTE) is the house strategy. Around it sits a catalog of ICT and Smart Money
plays that can be switched on, off, or into shadow individually, and combined.

Two rules hold the whole thing together:

*   A probability tier is **earned by calibration, never declared**. Anything
    uncalibrated is UNPROVEN and cannot open a trade by itself.
*   Confluence counts **families, not strategies**, so a catalog cannot
    manufacture conviction just by growing.
"""

from .catalog import (
    CATALOG,
    MIN_CALIBRATION_SAMPLE,
    Calibration,
    ProbabilityTier,
    StrategyFamily,
    StrategyId,
    StrategyProfile,
    by_family,
    calibrated,
    profile,
)
from .patterns import (
    BalancedPriceRange,
    all_fvgs,
    detect_bpr,
    detect_breaker,
    overlaps,
    unfilled,
)
from .playbook import (
    CONFLUENCE_BONUS,
    MAX_CONFLUENCE_FAMILIES,
    ConflictPolicy,
    GateResult,
    PlaybookConfig,
    PlaybookVerdict,
    SideReading,
    evaluate,
    score_verdict,
)
from .plays import PLAYS
from .registry import (
    Activation,
    StrategyRegistry,
    UnavailableStrategyError,
    registry_from_names,
)
from .scoring_bridge import (
    PILLAR_FACTORS,
    PRICING_PILLAR,
    VetoCheck,
    pillar_summary,
    score_setup,
)
from .sessions import Killzone, SessionClock, session_config
from .signals import (
    StrategyContext,
    StrategyEvaluator,
    StrategySignal,
    abstain,
    build_context,
    fire,
)
from .six_pillars import (
    DEFAULT_SWING_GRADE,
    Pillar,
    PillarFinding,
    SixPillarSetup,
    locate_six_pillars,
)

__all__ = [
    "Activation", "BalancedPriceRange", "CATALOG", "CONFLUENCE_BONUS",
    "Calibration", "ConflictPolicy", "DEFAULT_SWING_GRADE", "GateResult",
    "Killzone", "MAX_CONFLUENCE_FAMILIES", "MIN_CALIBRATION_SAMPLE",
    "PILLAR_FACTORS", "PLAYS", "PRICING_PILLAR", "Pillar", "PillarFinding",
    "PlaybookConfig", "PlaybookVerdict", "ProbabilityTier", "SessionClock",
    "SideReading", "SixPillarSetup", "StrategyContext", "StrategyEvaluator",
    "StrategyFamily", "StrategyId", "StrategyProfile", "StrategyRegistry",
    "StrategySignal", "UnavailableStrategyError", "VetoCheck", "abstain",
    "all_fvgs", "build_context", "by_family", "calibrated", "detect_bpr",
    "detect_breaker", "evaluate", "fire", "locate_six_pillars", "overlaps",
    "pillar_summary", "profile", "registry_from_names", "score_setup",
    "score_verdict", "session_config", "unfilled",
]
