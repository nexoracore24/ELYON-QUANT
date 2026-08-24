"""The strategy catalog: what exists, and what it has actually earned.

Every strategy carries a probability tier -- HIGH, MEDIUM, LOW -- and the tier
decides how much freedom it gets: whether it may open a trade alone, whether it
needs corroboration, whether it may only watch.

The important rule in this module is that **a tier is earned, never declared**.
An author's opinion of their own strategy is a hypothesis. Until a calibration
run backs it with a real sample, the effective tier is UNPROVEN and the strategy
cannot trade by itself, no matter how confident the docstring sounds. That is
the difference between a system that compounds and one that discovers its
mistakes with real money.

Two consequences worth stating outright:

*   A 90%-win-rate strategy with negative expectancy is LOW. Winning often and
    making money are different claims, and only the second one pays.
*   A strategy with a great record over 12 trades is UNPROVEN. Twelve trades is
    a story, not evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final, Mapping

from elyon.shared_kernel.edcs.numeric import ZERO, dec

# Below this many closed trades, a record is an anecdote.
MIN_CALIBRATION_SAMPLE: Final[int] = 30

# Expectancy is measured in R -- multiples of the risk taken -- so it is
# comparable across instruments and position sizes in a way win rate is not.
HIGH_EXPECTANCY_R: Final[Decimal] = dec("0.35")
MEDIUM_EXPECTANCY_R: Final[Decimal] = dec("0.15")


class ProbabilityTier(str, Enum):
    """How much the system is willing to trust a strategy.

    Ordered deliberately: UNPROVEN is not "bad", it is "unmeasured", and it is
    the only honest starting point for anything that has not been backtested.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNPROVEN = "UNPROVEN"

    @property
    def badge(self) -> str:
        """The marker shown in the UI and in printed reports."""
        return {
            ProbabilityTier.HIGH: "🟢",
            ProbabilityTier.MEDIUM: "🟡",
            ProbabilityTier.LOW: "🔴",
            ProbabilityTier.UNPROVEN: "⚪",
        }[self]

    @property
    def corroboration_required(self) -> int:
        """How many *other* independent families must agree before it trades.

        A HIGH strategy has earned the right to act alone. An UNPROVEN one can
        never open a trade by itself -- it may only add weight to something
        else -- which is what makes it safe to ship a large catalog.
        """
        return {
            ProbabilityTier.HIGH: 0,
            ProbabilityTier.MEDIUM: 1,
            ProbabilityTier.LOW: 2,
            ProbabilityTier.UNPROVEN: 99,  # effectively never alone
        }[self]

    @property
    def weight(self) -> Decimal:
        """How much this strategy's confidence counts in the blend."""
        return {
            ProbabilityTier.HIGH: dec("1.0"),
            ProbabilityTier.MEDIUM: dec("0.7"),
            ProbabilityTier.LOW: dec("0.4"),
            ProbabilityTier.UNPROVEN: dec("0.15"),
        }[self]


class StrategyFamily(str, Enum):
    """What a strategy is fundamentally reading.

    Families exist so confluence cannot be gamed. Five strategies that all read
    the same imbalance are one piece of evidence seen five times, and counting
    them as five would let a catalog manufacture false conviction just by
    growing. Confluence counts *families*, never strategies.
    """

    LIQUIDITY_RAID = "LIQUIDITY_RAID"        # stops taken, then rejection
    STRUCTURE_SHIFT = "STRUCTURE_SHIFT"      # BOS / CHoCH / MSS
    IMBALANCE = "IMBALANCE"                  # FVG, BPR, voids
    BLOCK_MITIGATION = "BLOCK_MITIGATION"    # order blocks, breakers
    PREMIUM_DISCOUNT = "PREMIUM_DISCOUNT"    # fib, OTE, dealing range
    SESSION_TIMING = "SESSION_TIMING"        # killzones, Power of 3
    CORRELATION = "CORRELATION"              # SMT and friends


class StrategyId(str, Enum):
    """Every play the engine knows how to look for."""

    SIX_PILLARS = "SIX_PILLARS"
    ICT_2022_MODEL = "ICT_2022_MODEL"
    ICT_SILVER_BULLET = "ICT_SILVER_BULLET"
    ICT_TURTLE_SOUP = "ICT_TURTLE_SOUP"
    ICT_UNICORN = "ICT_UNICORN"
    ICT_JUDAS_SWING = "ICT_JUDAS_SWING"
    ICT_OTE = "ICT_OTE"
    ICT_POWER_OF_3 = "ICT_POWER_OF_3"
    BREAKER_RETEST = "BREAKER_RETEST"
    BALANCED_PRICE_RANGE = "BALANCED_PRICE_RANGE"
    EQUAL_LEVEL_RAID = "EQUAL_LEVEL_RAID"
    ASIAN_RANGE_SWEEP = "ASIAN_RANGE_SWEEP"
    SMT_DIVERGENCE = "SMT_DIVERGENCE"


@dataclass(frozen=True, slots=True)
class Calibration:
    """What a strategy did on data it had not seen.

    This is the evidence a tier rests on. It is deliberately not optional to
    reason about: a strategy with no calibration gets UNPROVEN, and the absence
    is visible rather than papered over with a default.
    """

    sample_size: int
    wins: int
    expectancy_r: Decimal
    max_drawdown_r: Decimal = ZERO
    dataset: str = ""

    def __post_init__(self) -> None:
        if self.sample_size < 0 or self.wins < 0:
            raise ValueError("calibration counts cannot be negative")
        if self.wins > self.sample_size:
            raise ValueError(
                f"{self.wins} wins out of {self.sample_size} trades is impossible"
            )

    @property
    def win_rate(self) -> Decimal:
        if self.sample_size == 0:
            return ZERO
        return dec(self.wins) / dec(self.sample_size)

    @property
    def is_sufficient(self) -> bool:
        return self.sample_size >= MIN_CALIBRATION_SAMPLE

    @property
    def tier(self) -> ProbabilityTier:
        """The tier this record supports -- nothing more.

        Expectancy leads, because it is the only figure that answers "does this
        make money". A strategy that wins nine times out of ten and gives it all
        back on the tenth is not high probability, it is a slow loss.
        """
        if not self.is_sufficient:
            return ProbabilityTier.UNPROVEN
        if self.expectancy_r <= ZERO:
            return ProbabilityTier.LOW
        if self.expectancy_r >= HIGH_EXPECTANCY_R:
            return ProbabilityTier.HIGH
        if self.expectancy_r >= MEDIUM_EXPECTANCY_R:
            return ProbabilityTier.MEDIUM
        return ProbabilityTier.LOW


@dataclass(frozen=True, slots=True)
class StrategyProfile:
    """Everything the platform knows about one play."""

    id: StrategyId
    title: str
    family: StrategyFamily
    thesis: str
    declared_tier: ProbabilityTier
    calibration: Calibration | None = None
    requires_sessions: bool = False
    requires_correlated_feed: bool = False

    @property
    def effective_tier(self) -> ProbabilityTier:
        """The tier the system actually acts on.

        The author's declared tier is a prior and is shown for comparison only.
        Acting on it would let a confident docstring size a position.
        """
        if self.calibration is None:
            return ProbabilityTier.UNPROVEN
        return self.calibration.tier

    @property
    def is_proven(self) -> bool:
        return self.effective_tier is not ProbabilityTier.UNPROVEN

    @property
    def available(self) -> bool:
        """Whether this strategy can run on the data the platform has today."""
        return not self.requires_correlated_feed

    @property
    def tier_drift(self) -> str | None:
        """Where the author's belief and the evidence disagree.

        Worth surfacing: a strategy declared HIGH that calibrates LOW is the
        single most useful thing a research log can tell you.
        """
        if self.calibration is None:
            return f"declared {self.declared_tier.value}, never calibrated"
        if self.effective_tier is not self.declared_tier:
            return (
                f"declared {self.declared_tier.value}, "
                f"measured {self.effective_tier.value}"
            )
        return None

    def to_canonical_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "id": self.id.value,
            "family": self.family.value,
            "declaredTier": self.declared_tier.value,
            "effectiveTier": self.effective_tier.value,
        }
        if self.calibration is not None:
            out["calibrationSample"] = self.calibration.sample_size
            out["calibrationDataset"] = self.calibration.dataset
        return out


def _profile(
    id: StrategyId,
    title: str,
    family: StrategyFamily,
    thesis: str,
    declared: ProbabilityTier,
    **kwargs: object,
) -> StrategyProfile:
    return StrategyProfile(
        id=id, title=title, family=family, thesis=thesis,
        declared_tier=declared, **kwargs,  # type: ignore[arg-type]
    )


# The catalog. Note that every declared tier here is a *hypothesis*: nothing
# carries a Calibration yet, so every effective tier is UNPROVEN and nothing
# trades alone. Filling these in is the job of the Backtesting Engine, and the
# gap between the two columns is the research backlog.
CATALOG: Final[Mapping[StrategyId, StrategyProfile]] = {
    p.id: p for p in (
        _profile(
            StrategyId.SIX_PILLARS, "Six Pillars",
            StrategyFamily.LIQUIDITY_RAID,
            "Trend, liquidity taken, block, imbalance, fib and OTE all aligned. "
            "The house model: the strictest read, and the rarest.",
            ProbabilityTier.HIGH,
        ),
        _profile(
            StrategyId.ICT_2022_MODEL, "ICT 2022 Model",
            StrategyFamily.STRUCTURE_SHIFT,
            "Liquidity sweep, then a market structure shift against it, then "
            "entry on the FVG the shift left behind.",
            ProbabilityTier.HIGH,
        ),
        _profile(
            StrategyId.ICT_SILVER_BULLET, "Silver Bullet",
            StrategyFamily.SESSION_TIMING,
            "A single hour, a single FVG, in the direction of the session bias. "
            "Outside the hour the pattern means nothing.",
            ProbabilityTier.MEDIUM, requires_sessions=True,
        ),
        _profile(
            StrategyId.ICT_TURTLE_SOUP, "Turtle Soup",
            StrategyFamily.LIQUIDITY_RAID,
            "An old high or low breaks, fails, and closes back inside. The "
            "breakout traders are the liquidity.",
            ProbabilityTier.MEDIUM,
        ),
        _profile(
            StrategyId.ICT_UNICORN, "Unicorn Model",
            StrategyFamily.BLOCK_MITIGATION,
            "A breaker block and a fair value gap occupying the same prices. "
            "Two independent reasons for one zone.",
            ProbabilityTier.HIGH,
        ),
        _profile(
            StrategyId.ICT_JUDAS_SWING, "Judas Swing",
            StrategyFamily.SESSION_TIMING,
            "The opening move is a lie: price runs one way to collect stops, "
            "then spends the session going the other.",
            ProbabilityTier.MEDIUM, requires_sessions=True,
        ),
        _profile(
            StrategyId.ICT_OTE, "Optimal Trade Entry",
            StrategyFamily.PREMIUM_DISCOUNT,
            "Retracement into 0.618-0.786 of an impulsive leg. Never alone -- "
            "a retracement level is a price, not a reason.",
            ProbabilityTier.LOW,
        ),
        _profile(
            StrategyId.ICT_POWER_OF_3, "Power of 3 (AMD)",
            StrategyFamily.SESSION_TIMING,
            "Accumulate, manipulate, distribute. The session's range is built "
            "before the move that matters.",
            ProbabilityTier.MEDIUM, requires_sessions=True,
        ),
        _profile(
            StrategyId.BREAKER_RETEST, "Breaker Retest",
            StrategyFamily.BLOCK_MITIGATION,
            "An order block that failed becomes resistance on the way back. "
            "The level that did not hold is the level that now rejects.",
            ProbabilityTier.MEDIUM,
        ),
        _profile(
            StrategyId.BALANCED_PRICE_RANGE, "Balanced Price Range",
            StrategyFamily.IMBALANCE,
            "Two opposing fair value gaps overlapping: price traded both "
            "directions too fast and left a pocket it tends to revisit.",
            ProbabilityTier.LOW,
        ),
        _profile(
            StrategyId.EQUAL_LEVEL_RAID, "Equal Level Raid",
            StrategyFamily.LIQUIDITY_RAID,
            "Equal highs or lows are an advertisement. Price collects them and "
            "reverses.",
            ProbabilityTier.MEDIUM,
        ),
        _profile(
            StrategyId.ASIAN_RANGE_SWEEP, "Asian Range Sweep",
            StrategyFamily.SESSION_TIMING,
            "London takes one side of the overnight range before choosing a "
            "direction for the day.",
            ProbabilityTier.MEDIUM, requires_sessions=True,
        ),
        _profile(
            StrategyId.SMT_DIVERGENCE, "SMT Divergence",
            StrategyFamily.CORRELATION,
            "Correlated instruments disagree at a high or low: one makes it, "
            "the other refuses. The refusal is the tell.",
            ProbabilityTier.HIGH, requires_correlated_feed=True,
        ),
    )
}


def profile(strategy: StrategyId) -> StrategyProfile:
    return CATALOG[strategy]


def by_family(family: StrategyFamily) -> tuple[StrategyProfile, ...]:
    return tuple(p for p in CATALOG.values() if p.family is family)


def calibrated(
    strategy: StrategyId, calibration: Calibration
) -> StrategyProfile:
    """Attach evidence to a profile, returning the updated copy.

    This is how a strategy graduates: it runs in shadow, the Backtesting Engine
    produces a record, and the record -- not an opinion -- moves the tier.
    """
    from dataclasses import replace

    return replace(CATALOG[strategy], calibration=calibration)
