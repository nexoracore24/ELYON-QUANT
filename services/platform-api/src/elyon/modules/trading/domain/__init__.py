"""Trading Engine domain -- scoring, decisions and explanations."""

from .explanation import DecisionRecord, Explanation, Provenance, explain
from .scoring import (
    DEFAULT_ENTRY_THRESHOLD,
    DEFAULT_WEIGHTS,
    Conviction,
    Factor,
    FactorScore,
    Score,
    ScoreBuilder,
    Veto,
    VetoCheck,
    max_possible,
    validate_weights,
)

__all__ = [
    "DEFAULT_ENTRY_THRESHOLD", "DEFAULT_WEIGHTS", "Conviction",
    "DecisionRecord", "Explanation", "Factor", "FactorScore", "Provenance",
    "Score", "ScoreBuilder", "Veto", "VetoCheck", "explain", "max_possible",
    "validate_weights",
]
