"""Combining active strategies into one decision.

This is the module where a strategy catalog either becomes an edge or becomes a
trade-count maximiser, so the rules are worth stating plainly.

**Agreement is counted by family, not by strategy.** Five plays reading the same
fair value gap are one piece of evidence seen five times. Counting them as five
would let the catalog manufacture conviction simply by growing, which is exactly
backwards: adding a strategy should never, by itself, make an existing setup
look better.

**Disagreement is a veto, not an average.** When two live strategies want
opposite sides, netting them out produces a small position in whichever one
happened to be louder, and hides the fact that the engine had no idea. The
engine says so instead and stands down.

**Tier decides who may act alone.** A HIGH strategy has earned that right by
calibration. An UNPROVEN one has not, and may only add weight to something else.
That single rule is what makes it safe to ship thirteen strategies at once.

**Shadow signals never touch the trade.** They are evaluated, recorded, and
ignored -- which is how an unproven strategy accumulates the evidence it needs
to stop being unproven.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Mapping

from elyon.modules.smart_money.domain.structure import Direction
from elyon.modules.trading.domain.scoring import Score, Veto
from elyon.shared_kernel.edcs.numeric import ONE, ZERO, dec, quantize_ratio

from .catalog import (
    Calibration,
    ProbabilityTier,
    StrategyFamily,
    StrategyId,
    profile,
)
from .registry import StrategyRegistry
from .scoring_bridge import score_setup
from .signals import StrategyContext, StrategySignal
from .plays import PLAYS

# Confluence stops paying after this many agreeing families. Beyond it the
# marginal family is telling you something you already knew, and letting the
# multiplier run unbounded turns a crowded chart into a false certainty.
MAX_CONFLUENCE_FAMILIES = 4

# What agreeing families are worth, as a multiplier on the blended confidence.
CONFLUENCE_BONUS: Mapping[int, Decimal] = {
    1: dec("1.00"),
    2: dec("1.15"),
    3: dec("1.28"),
    4: dec("1.35"),
}


class ConflictPolicy(str, Enum):
    """What to do when live strategies want opposite sides."""

    VETO = "VETO"                # stand down -- the default, and the safe one
    STRONGEST_WINS = "STRONGEST_WINS"   # trade the heavier side, if it is clear
    MAJORITY = "MAJORITY"        # trade the side with more agreeing families


class GateResult(str, Enum):
    """Why the playbook did or did not reach a tradeable conclusion."""

    PASSED = "PASSED"
    NO_SIGNALS = "NO_SIGNALS"
    CONFLICTED = "CONFLICTED"
    INSUFFICIENT_CORROBORATION = "INSUFFICIENT_CORROBORATION"


@dataclass(frozen=True, slots=True)
class PlaybookConfig:
    """How this account wants its strategies combined.

    ``calibrations`` is how evidence reaches the tier system without mutating
    the catalog: the Backtesting Engine produces records, they are supplied
    here, and the tiers move. An empty mapping means nothing has been measured
    and everything is UNPROVEN, which is the correct state on day one.
    """

    conflict_policy: ConflictPolicy = ConflictPolicy.VETO
    # How much clearer the winning side must be for STRONGEST_WINS to act.
    dominance_margin: Decimal = dec("0.25")
    calibrations: Mapping[StrategyId, Calibration] = field(default_factory=dict)

    def tier_of(self, strategy: StrategyId) -> ProbabilityTier:
        """The tier the engine acts on, evidence first."""
        record = self.calibrations.get(strategy)
        if record is not None:
            return record.tier
        return profile(strategy).effective_tier

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "conflictPolicy": self.conflict_policy.value,
            "dominanceMargin": str(self.dominance_margin),
            "calibrated": sorted(s.value for s in self.calibrations),
        }


@dataclass(frozen=True, slots=True)
class SideReading:
    """Everything the live strategies said about one direction."""

    direction: Direction
    signals: tuple[StrategySignal, ...]
    families: tuple[StrategyFamily, ...]
    weighted_confidence: Decimal
    best_tier: ProbabilityTier

    @property
    def confluence(self) -> int:
        return len(self.families)


@dataclass(frozen=True, slots=True)
class PlaybookVerdict:
    """What the active strategies concluded, and why.

    Carries the abstentions and the shadow signals as well as the firing ones,
    so a decision can be reconstructed later without re-running anything.
    """

    direction: Direction | None
    gate: GateResult
    reason: str
    live_signals: tuple[StrategySignal, ...]
    shadow_signals: tuple[StrategySignal, ...]
    sides: tuple[SideReading, ...]
    confidence: Decimal
    registry_hash: str

    @property
    def fired(self) -> tuple[StrategySignal, ...]:
        return tuple(s for s in self.live_signals if s.fired)

    @property
    def abstained(self) -> tuple[StrategySignal, ...]:
        return tuple(s for s in self.live_signals if not s.fired)

    @property
    def agreeing(self) -> SideReading | None:
        if self.direction is None:
            return None
        return next((s for s in self.sides if s.direction is self.direction), None)

    @property
    def confluence(self) -> int:
        side = self.agreeing
        return side.confluence if side is not None else 0

    @property
    def tradeable(self) -> bool:
        return self.gate is GateResult.PASSED and self.direction is not None

    def summary(self) -> str:
        """One line per evaluated strategy, live first."""
        lines = [str(s) for s in self.live_signals]
        lines += [f"  (shadow) {s}" for s in self.shadow_signals]
        return "\n".join(lines)


def _read_side(
    direction: Direction,
    signals: tuple[StrategySignal, ...],
    config: PlaybookConfig,
) -> SideReading:
    """Blend one side's signals, counting each family once.

    Within a family the strongest signal stands for the family. Averaging them
    would let a weak duplicate dilute a strong read, and summing them would pay
    for the duplication.
    """
    def weight(signal: StrategySignal) -> Decimal:
        return signal.confidence * config.tier_of(signal.strategy).weight

    by_family: dict[StrategyFamily, StrategySignal] = {}
    for signal in signals:
        family = profile(signal.strategy).family
        current = by_family.get(family)
        if current is None or weight(signal) > weight(current):
            by_family[family] = signal

    families = tuple(sorted(by_family, key=lambda f: f.value))
    total = sum((weight(by_family[f]) for f in families), ZERO)
    blended = ZERO if not families else total / dec(len(families))

    bonus = CONFLUENCE_BONUS.get(
        min(len(families), MAX_CONFLUENCE_FAMILIES), dec("1.35")
    )
    confidence = min(ONE, quantize_ratio(blended * bonus))

    order = list(ProbabilityTier)
    best = min(
        (config.tier_of(s.strategy) for s in signals),
        key=order.index,
        default=ProbabilityTier.UNPROVEN,
    )
    return SideReading(direction, signals, families, confidence, best)


def evaluate(
    context: StrategyContext,
    registry: StrategyRegistry,
    *,
    config: PlaybookConfig | None = None,
) -> PlaybookVerdict:
    """Run every evaluated strategy and combine the live ones."""
    settings = config or PlaybookConfig()

    live: list[StrategySignal] = []
    shadow: list[StrategySignal] = []
    for strategy in registry.evaluated_ids:
        signal = PLAYS[strategy](context)
        if registry.is_live(strategy):
            live.append(signal)
        else:
            shadow.append(signal)

    up = tuple(s for s in live if s.direction is Direction.UP)
    down = tuple(s for s in live if s.direction is Direction.DOWN)

    sides: list[SideReading] = []
    if up:
        sides.append(_read_side(Direction.UP, up, settings))
    if down:
        sides.append(_read_side(Direction.DOWN, down, settings))

    verdict = _decide(sides, settings)
    return PlaybookVerdict(
        direction=verdict[0],
        gate=verdict[1],
        reason=verdict[2],
        live_signals=tuple(live),
        shadow_signals=tuple(shadow),
        sides=tuple(sides),
        confidence=verdict[3],
        registry_hash=registry.config_hash,
    )


def _decide(
    sides: list[SideReading], config: PlaybookConfig
) -> tuple[Direction | None, GateResult, str, Decimal]:
    if not sides:
        return None, GateResult.NO_SIGNALS, "no live strategy fired", ZERO

    if len(sides) > 1:
        resolved = _resolve_conflict(sides, config)
        if resolved is None:
            names = ", ".join(
                f"{s.direction.name}({len(s.signals)})" for s in sides
            )
            return (
                None,
                GateResult.CONFLICTED,
                f"live strategies disagree: {names}",
                ZERO,
            )
        chosen = resolved
    else:
        chosen = sides[0]

    # Corroboration: the best-tier strategy on this side sets the bar, and
    # *other* families have to meet it.
    required = chosen.best_tier.corroboration_required
    corroborating = chosen.confluence - 1
    if corroborating < required:
        if chosen.best_tier is ProbabilityTier.UNPROVEN:
            detail = (
                f"{chosen.direction.name} is backed only by uncalibrated "
                f"strategies, which may corroborate a proven one but never "
                f"open a trade alone"
            )
        else:
            detail = (
                f"{chosen.direction.name} is backed by {chosen.best_tier.value} "
                f"strategies, which need {required} corroborating "
                f"famil{'y' if required == 1 else 'ies'}; {corroborating} agreed"
            )
        return None, GateResult.INSUFFICIENT_CORROBORATION, detail, \
            chosen.weighted_confidence

    families = ", ".join(f.value for f in chosen.families)
    return (
        chosen.direction,
        GateResult.PASSED,
        f"{chosen.confluence} agreeing famil"
        f"{'y' if chosen.confluence == 1 else 'ies'} ({families})",
        chosen.weighted_confidence,
    )


def _resolve_conflict(
    sides: list[SideReading], config: PlaybookConfig
) -> SideReading | None:
    """Pick a side when the strategies disagree, or refuse to."""
    if config.conflict_policy is ConflictPolicy.VETO:
        return None

    ranked = sorted(
        sides, key=lambda s: (s.weighted_confidence, s.confluence), reverse=True
    )
    best, rest = ranked[0], ranked[1]

    if config.conflict_policy is ConflictPolicy.MAJORITY:
        # A tie in family count is still a disagreement.
        return best if best.confluence > rest.confluence else None

    # STRONGEST_WINS: the margin exists so a near-tie is still treated as the
    # disagreement it is.
    if best.weighted_confidence - rest.weighted_confidence >= config.dominance_margin:
        return best
    return None


def score_verdict(
    context: StrategyContext,
    verdict: PlaybookVerdict,
    *,
    threshold: int | None = None,
) -> Score:
    """Fold a playbook verdict into the factor score.

    The six-pillar factors still do the scoring -- the playbook does not invent
    a second currency -- but a conflict enters as a veto, because "the engine
    disagreed with itself" is a reason to stand down rather than a reason to
    score lower.
    """
    vetoes: list[tuple[Veto, bool, str]] = [
        (
            Veto.STRATEGY_CONFLICT,
            verdict.gate is GateResult.CONFLICTED,
            verdict.reason if verdict.gate is GateResult.CONFLICTED
            else "live strategies agree on a side",
        ),
    ]
    return score_setup(context.setup, threshold=threshold, vetoes=vetoes)
