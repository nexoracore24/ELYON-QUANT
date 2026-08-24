"""The six-pillar strategy as a first-class object.

TENDENCIA · LIQUIDEZ · ORDER BLOCK · FVG · FIBONACCI · ZONA OTE -- located in a
single pass, scored through the same factor table as everything else, and always
reported with the reason each pillar stood or fell.
"""

from .scoring_bridge import (
    PILLAR_FACTORS,
    PRICING_PILLAR,
    VetoCheck,
    pillar_summary,
    score_setup,
)
from .six_pillars import (
    DEFAULT_SWING_GRADE,
    Pillar,
    PillarFinding,
    SixPillarSetup,
    locate_six_pillars,
)

__all__ = [
    "DEFAULT_SWING_GRADE",
    "PILLAR_FACTORS",
    "PRICING_PILLAR",
    "Pillar",
    "PillarFinding",
    "SixPillarSetup",
    "VetoCheck",
    "locate_six_pillars",
    "pillar_summary",
    "score_setup",
]
