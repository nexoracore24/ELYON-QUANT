"""The Scoring Engine.

A weighted sum of objective factors, which is the whole reason this engine can
explain itself: the contribution of every criterion is the arithmetic itself,
not an approximation reconstructed afterwards. There is no opaque model to
interrogate -- the score *is* the explanation.

Two rules shape the design. Vetoes block rather than subtract, because a
news window or a blown-out spread is not something a strong setup should be
able to outvote. And context is not scored here at all: the Market Context
Engine already gated on it, so counting killzone or volatility again would be
counting the same evidence twice (ADR-0008).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Final, Iterable, Mapping, Sequence

from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec, dsum

DEFAULT_ENTRY_THRESHOLD: Final[int] = 70
DEFAULT_HIGH_CONVICTION: Final[int] = 85
WATCHLIST_FLOOR: Final[int] = 55


class Factor(str, Enum):
    """What the engine is allowed to score.

    Setup confluence only. Killzone and volatility regime are deliberately
    absent: they belong to the context gate, not to the score.
    """

    HTF_BIAS = "HTF_BIAS"                  # alignment with the higher-timeframe read
    STRUCTURE = "STRUCTURE"                # CHoCH or BOS with displacement
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"    # the opposing side was taken first
    POI_QUALITY = "POI_QUALITY"            # unmitigated order block / breaker
    IMBALANCE = "IMBALANCE"                # FVG or inverse FVG in confluence
    PRICING = "PRICING"                    # discount for longs, premium for shorts
    OTE_FIBONACCI = "OTE_FIBONACCI"        # inside the optimal entry band
    VOLUME = "VOLUME"                      # participation confirming the move
    TARGET_LIQUIDITY = "TARGET_LIQUIDITY"  # somewhere worth travelling to


# Renormalised per ADR-0008 after removing the context factors. Calibration
# against real data (ENG-004) will move these; the shape is what matters now.
DEFAULT_WEIGHTS: Final[Mapping[Factor, int]] = {
    Factor.HTF_BIAS: 17,
    Factor.STRUCTURE: 17,
    Factor.LIQUIDITY_SWEEP: 14,
    Factor.POI_QUALITY: 14,
    Factor.IMBALANCE: 12,
    Factor.PRICING: 9,
    Factor.OTE_FIBONACCI: 7,
    Factor.VOLUME: 5,
    Factor.TARGET_LIQUIDITY: 5,
}


class Veto(str, Enum):
    """Conditions that block a trade outright, whatever the score says."""

    NEWS_WINDOW = "NEWS_WINDOW"
    SPREAD_BLOWOUT = "SPREAD_BLOWOUT"
    CONTEXT_GATE_FAILED = "CONTEXT_GATE_FAILED"
    RISK_LIMIT = "RISK_LIMIT"
    KILL_SWITCH = "KILL_SWITCH"
    STALE_DATA = "STALE_DATA"
    BIAS_CONFLICT = "BIAS_CONFLICT"


class Conviction(str, Enum):
    DISCARD = "DISCARD"        # below the watchlist floor
    WATCHLIST = "WATCHLIST"    # interesting, not tradeable
    STANDARD = "STANDARD"      # above threshold
    HIGH = "HIGH"              # earns a larger share of risk


@dataclass(frozen=True, slots=True)
class FactorScore:
    """One criterion's contribution, with the reason it earned it."""

    factor: Factor
    weight: int
    awarded: int
    condition: str

    def __post_init__(self) -> None:
        if not 0 <= self.awarded <= self.weight:
            raise DeterminismError(
                f"{self.factor.value} awarded {self.awarded} of a possible "
                f"{self.weight}"
            )

    @property
    def satisfied(self) -> bool:
        return self.awarded > 0


@dataclass(frozen=True, slots=True)
class VetoCheck:
    veto: Veto
    active: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Score:
    """A complete, self-explaining verdict."""

    total: int
    threshold: int
    factors: tuple[FactorScore, ...]
    vetoes: tuple[VetoCheck, ...]

    @property
    def blocking_vetoes(self) -> tuple[VetoCheck, ...]:
        return tuple(v for v in self.vetoes if v.active)

    @property
    def is_vetoed(self) -> bool:
        return bool(self.blocking_vetoes)

    @property
    def conviction(self) -> Conviction:
        if self.is_vetoed or self.total < WATCHLIST_FLOOR:
            return Conviction.DISCARD
        if self.total < self.threshold:
            return Conviction.WATCHLIST
        if self.total >= DEFAULT_HIGH_CONVICTION:
            return Conviction.HIGH
        return Conviction.STANDARD

    @property
    def tradeable(self) -> bool:
        return self.conviction in (Conviction.STANDARD, Conviction.HIGH)

    @property
    def confirmed(self) -> tuple[FactorScore, ...]:
        return tuple(f for f in self.factors if f.satisfied)

    @property
    def discarded(self) -> tuple[FactorScore, ...]:
        return tuple(f for f in self.factors if not f.satisfied)

    @property
    def primary_reason(self) -> str:
        """The single reason that decided it -- vetoes first, then the score."""
        blocking = self.blocking_vetoes
        if blocking:
            return f"veto:{blocking[0].veto.value.lower()}"
        if self.total < self.threshold:
            return "score_below_threshold"
        return "entered"


class ScoreBuilder:
    """Accumulates factors and vetoes into a Score.

    Each factor may be recorded once. A double entry would silently inflate the
    total and quietly break the explanation, so it raises instead.
    """

    def __init__(
        self,
        *,
        weights: Mapping[Factor, int] | None = None,
        threshold: int = DEFAULT_ENTRY_THRESHOLD,
    ) -> None:
        self._weights = dict(weights) if weights is not None else dict(DEFAULT_WEIGHTS)
        self._threshold = threshold
        self._factors: dict[Factor, FactorScore] = {}
        self._vetoes: list[VetoCheck] = []

    def award(self, factor: Factor, condition: str, *, fraction: str = "1") -> "ScoreBuilder":
        """Credit a factor, in full or in part.

        Partial credit exists because confluence is rarely binary: a block that
        is technically unmitigated but already tapped once is worth something,
        just not everything.
        """
        if factor in self._factors:
            raise DeterminismError(f"{factor.value} scored twice")
        weight = self._weights.get(factor, 0)
        share = dec(fraction)
        if not ZERO <= share <= dec("1"):
            raise DeterminismError(f"fraction must be within [0,1], got {share}")
        awarded = int((share * weight).to_integral_value())
        self._factors[factor] = FactorScore(factor, weight, awarded, condition)
        return self

    def withhold(self, factor: Factor, reason: str) -> "ScoreBuilder":
        """Record a criterion that was checked and *not* met.

        Explicitly logging the misses is what lets the engine answer "why not"
        as precisely as it answers "why".
        """
        if factor in self._factors:
            raise DeterminismError(f"{factor.value} scored twice")
        self._factors[factor] = FactorScore(factor, self._weights.get(factor, 0), 0, reason)
        return self

    def check_veto(self, veto: Veto, active: bool, reason: str) -> "ScoreBuilder":
        self._vetoes.append(VetoCheck(veto, active, reason))
        return self

    def build(self) -> Score:
        # Fixed factor order so the same evidence always reads the same way.
        ordered = tuple(
            self._factors[f] for f in Factor if f in self._factors
        )
        total = int(dsum(dec(f.awarded) for f in ordered))
        return Score(total, self._threshold, ordered, tuple(self._vetoes))


def max_possible(weights: Mapping[Factor, int] | None = None) -> int:
    """The ceiling of the weight table -- should be 100 for a sane config."""
    table = weights if weights is not None else DEFAULT_WEIGHTS
    return sum(table.values())


def validate_weights(weights: Mapping[Factor, int]) -> None:
    """Reject a weight table that cannot produce a meaningful 0-100 score."""
    if any(w < 0 for w in weights.values()):
        raise DeterminismError("factor weights cannot be negative")
    total = sum(weights.values())
    if total != 100:
        raise DeterminismError(
            f"factor weights must sum to 100 so the score reads as a "
            f"percentage; got {total}"
        )
