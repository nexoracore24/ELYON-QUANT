"""Turning strategies on and off.

Activation is deliberately tri-state rather than a boolean, because a boolean
creates a deadlock the tier system cannot escape: a strategy needs calibration
data before it may trade, and it cannot produce calibration data without
running. SHADOW is the way out -- the strategy is evaluated on every bar and
its signals are recorded, but they never reach the trade. Run it in shadow,
collect the sample, promote it on the evidence.

The whole activation state hashes into one value that travels with every
decision, so a replay can prove which strategies were switched on when a trade
was taken. Without that, "why did it do this?" is unanswerable six months later.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Iterator, Mapping

from elyon.shared_kernel.edcs.canonical import config_hash
from elyon.shared_kernel.edcs.numeric import DeterminismError

from .catalog import CATALOG, ProbabilityTier, StrategyFamily, StrategyId, profile


class Activation(str, Enum):
    """What a strategy is allowed to do."""

    OFF = "OFF"          # not evaluated at all
    SHADOW = "SHADOW"    # evaluated and recorded, never influences a trade
    LIVE = "LIVE"        # evaluated and counted

    @property
    def is_evaluated(self) -> bool:
        return self is not Activation.OFF

    @property
    def influences_trades(self) -> bool:
        return self is Activation.LIVE


class UnavailableStrategyError(DeterminismError):
    """Raised when enabling a strategy the platform cannot actually run."""


@dataclass(frozen=True, slots=True)
class StrategyRegistry:
    """Which strategies are on, and in what mode.

    Immutable: every change returns a new registry. That keeps the activation
    state that produced a decision pinned to that decision, instead of being a
    mutable global that has already moved on by the time anyone investigates.
    """

    activations: Mapping[StrategyId, Activation]

    def __post_init__(self) -> None:
        for strategy, mode in self.activations.items():
            if strategy not in CATALOG:
                raise DeterminismError(f"unknown strategy {strategy}")
            if mode.influences_trades and not profile(strategy).available:
                raise UnavailableStrategyError(
                    f"{strategy.value} needs a correlated feed the platform "
                    f"does not have; it can run in SHADOW but not LIVE"
                )

    # -- construction -----------------------------------------------------

    @classmethod
    def all_off(cls) -> "StrategyRegistry":
        return cls({s: Activation.OFF for s in StrategyId})

    @classmethod
    def default(cls) -> "StrategyRegistry":
        """The shipping default: the house model live, everything else watching.

        Shipping thirteen strategies all switched on would be a trade-count
        maximiser wearing a catalog. The rest earn their way in.
        """
        return cls.all_off().live(StrategyId.SIX_PILLARS).shadow(
            *(s for s in StrategyId if s is not StrategyId.SIX_PILLARS)
        )

    def _with(self, updates: Mapping[StrategyId, Activation]) -> "StrategyRegistry":
        merged = dict(self.activations)
        merged.update(updates)
        return StrategyRegistry(merged)

    def live(self, *strategies: StrategyId) -> "StrategyRegistry":
        return self._with({s: Activation.LIVE for s in strategies})

    def shadow(self, *strategies: StrategyId) -> "StrategyRegistry":
        return self._with({s: Activation.SHADOW for s in strategies})

    def off(self, *strategies: StrategyId) -> "StrategyRegistry":
        return self._with({s: Activation.OFF for s in strategies})

    def only(self, *strategies: StrategyId) -> "StrategyRegistry":
        """Everything off except these, which go live."""
        return StrategyRegistry.all_off().live(*strategies)

    def live_family(self, family: StrategyFamily) -> "StrategyRegistry":
        return self.live(*(p.id for p in CATALOG.values() if p.family is family))

    # -- inspection -------------------------------------------------------

    def mode(self, strategy: StrategyId) -> Activation:
        return self.activations.get(strategy, Activation.OFF)

    def is_live(self, strategy: StrategyId) -> bool:
        return self.mode(strategy).influences_trades

    def is_evaluated(self, strategy: StrategyId) -> bool:
        return self.mode(strategy).is_evaluated

    def _sorted(self, predicate) -> tuple[StrategyId, ...]:
        # Sorted by the enum's declaration order so evaluation is deterministic
        # regardless of how the mapping was built.
        return tuple(s for s in StrategyId if predicate(self.mode(s)))

    @property
    def live_ids(self) -> tuple[StrategyId, ...]:
        return self._sorted(lambda m: m.influences_trades)

    @property
    def shadow_ids(self) -> tuple[StrategyId, ...]:
        return self._sorted(lambda m: m is Activation.SHADOW)

    @property
    def evaluated_ids(self) -> tuple[StrategyId, ...]:
        return self._sorted(lambda m: m.is_evaluated)

    def __iter__(self) -> Iterator[StrategyId]:
        return iter(self.evaluated_ids)

    def __len__(self) -> int:
        return len(self.evaluated_ids)

    # -- provenance -------------------------------------------------------

    def to_canonical_dict(self) -> dict[str, str]:
        """Sorted by strategy name so the hash never depends on insertion order."""
        return {
            s.value: self.mode(s).value
            for s in sorted(StrategyId, key=lambda x: x.value)
        }

    @property
    def config_hash(self) -> str:
        """Pins the activation state to every decision taken under it."""
        return config_hash(self.to_canonical_dict())

    # -- reporting --------------------------------------------------------

    def unproven_live(self) -> tuple[StrategyId, ...]:
        """Live strategies with no calibration behind them.

        Not an error -- the gating rules already stop them trading alone -- but
        it is the number a risk review should look at first.
        """
        return tuple(s for s in self.live_ids if not profile(s).is_proven)

    def summary(self) -> str:
        """The catalog as a trader would scan it."""
        lines = []
        for strategy in StrategyId:
            prof = profile(strategy)
            mode = self.mode(strategy)
            mark = {"LIVE": "●", "SHADOW": "◐", "OFF": "○"}[mode.value]
            tier = prof.effective_tier
            note = ""
            if not prof.available:
                note = "  (needs correlated feed)"
            elif drift := prof.tier_drift:
                note = f"  ({drift})"
            lines.append(
                f"{mark} {tier.badge} {prof.title:<24} "
                f"{prof.family.value:<18} {mode.value:<7}{note}"
            )
        return "\n".join(lines)


def registry_from_names(names: Iterable[str]) -> StrategyRegistry:
    """Build a registry from configuration -- names go live, the rest are off.

    Unknown names fail loudly. A typo that silently disables a strategy is the
    kind of bug that shows up as a quiet drop in performance months later.
    """
    chosen = []
    for name in names:
        try:
            chosen.append(StrategyId(name))
        except ValueError as exc:
            valid = ", ".join(s.value for s in StrategyId)
            raise DeterminismError(
                f"unknown strategy {name!r}; known strategies are: {valid}"
            ) from exc
    return StrategyRegistry.all_off().live(*chosen)
