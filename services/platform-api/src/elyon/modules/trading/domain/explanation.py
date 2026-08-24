"""Decision records and explanations.

Every evaluation produces a record -- the ones that traded and, just as
importantly, the ones that did not. A discarded setup is often the more
instructive of the two, and a system that only logs its trades cannot answer
"why didn't you take that?".

The narrative is generated from the record, never alongside it. That is what
makes the guarantee enforceable: an explanation cannot cite a factor the
decision did not actually weigh, because it has nothing else to read from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from elyon.shared_kernel.edcs.canonical import stable_id
from elyon.shared_kernel.edcs.numeric import DeterminismError
from .scoring import Conviction, Factor, Score


@dataclass(frozen=True, slots=True)
class Provenance:
    """What the decision was computed from.

    Without this a replay is guesswork: the same bar can yield a different
    verdict under different config, and only these hashes can tell you which
    one you are looking at.
    """

    data_version: str
    config_hash: str
    dna_hash: str | None = None

    def to_canonical_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "dataVersion": self.data_version,
            "configHash": self.config_hash,
        }
        if self.dna_hash is not None:
            out["dnaHash"] = self.dna_hash
        return out


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """An immutable record of one evaluation, traded or not."""

    symbol: str
    bar_close_time_ns: int
    side: str                # LONG | SHORT | NONE
    action: str              # enter_long | enter_short | no_trade
    score: Score
    provenance: Provenance
    detected: Mapping[str, str] = field(default_factory=dict)
    rejection_reason: str | None = None
    """Why the trade was refused *after* scoring -- typically a risk rule.

    A setup can clear the threshold and still not trade. Without this the
    record could only report the score's verdict, which would read as if the
    engine had entered.
    """

    @property
    def final_reason(self) -> str:
        """The reason that actually decided it, risk included."""
        if self.action == "no_trade" and self.rejection_reason:
            return self.rejection_reason
        return self.score.primary_reason

    @property
    def decision_id(self) -> str:
        """Derived from the business keys, so replay reproduces it exactly."""
        return str(
            stable_id(
                namespace="decision",
                key={
                    "symbol": self.symbol,
                    "barCloseTime": self.bar_close_time_ns,
                    "configHash": self.provenance.config_hash,
                    "side": self.side,
                },
            )
        )

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "decisionId": self.decision_id,
            "symbol": self.symbol,
            "barCloseTime": self.bar_close_time_ns,
            "side": self.side,
            "action": self.action,
            "score": self.score.total,
            "threshold": self.score.threshold,
            "primaryReason": self.final_reason,
            "factors": [
                {
                    "factor": f.factor.value,
                    "weight": f.weight,
                    "awarded": f.awarded,
                    "condition": f.condition,
                }
                for f in self.score.factors
            ],
            "vetoes": [
                {"veto": v.veto.value, "active": v.active, "reason": v.reason}
                for v in self.score.vetoes
            ],
            "detected": dict(self.detected),
            **self.provenance.to_canonical_dict(),
        }


@dataclass(frozen=True, slots=True)
class Explanation:
    """The seven things every decision must be able to answer."""

    decision_id: str
    action: str
    score: int
    threshold: int
    detected: tuple[str, ...]
    confirmed: tuple[str, ...]
    discarded: tuple[str, ...]
    weights: tuple[tuple[str, int, int], ...]  # factor, weight, awarded
    rules_fired: tuple[str, ...]
    vetoes_blocked: tuple[str, ...]
    primary_reason: str
    narrative: str

    def cites_only(self, known_factors: set[str]) -> bool:
        """Fidelity check: every factor named is one the decision actually weighed."""
        return {name for name, _, _ in self.weights} <= known_factors


def explain(record: DecisionRecord) -> Explanation:
    """Turn a decision record into its explanation.

    Derived purely from the record: there is no second source of truth to drift
    from, and nothing here can invent a reason the engine did not have.
    """
    score = record.score
    awarded_sum = sum(f.awarded for f in score.factors)
    if awarded_sum != score.total:
        raise DeterminismError(
            f"explanation would misreport the score: factors sum to "
            f"{awarded_sum} but the total is {score.total}"
        )

    confirmed = tuple(f"{f.factor.value}: {f.condition}" for f in score.confirmed)
    discarded = tuple(f"{f.factor.value}: {f.condition}" for f in score.discarded)
    blocked = tuple(f"{v.veto.value}: {v.reason}" for v in score.blocking_vetoes)

    return Explanation(
        decision_id=record.decision_id,
        action=record.action,
        score=score.total,
        threshold=score.threshold,
        detected=tuple(f"{k}: {v}" for k, v in sorted(record.detected.items())),
        confirmed=confirmed,
        discarded=discarded,
        weights=tuple((f.factor.value, f.weight, f.awarded) for f in score.factors),
        rules_fired=tuple(f.factor.value for f in score.confirmed),
        vetoes_blocked=blocked,
        primary_reason=record.final_reason,
        narrative=_narrate(record),
    )


def _narrate(record: DecisionRecord) -> str:
    """Compose the human-readable account, deterministically."""
    score = record.score
    head = (
        f"[{record.action.upper()} {record.symbol} · "
        f"score {score.total}/{max(score.threshold, score.total)} · "
        f"threshold {score.threshold}]"
    )

    parts = [head]
    if record.detected:
        detected = ", ".join(f"{k} {v}" for k, v in sorted(record.detected.items()))
        parts.append(f"Detected: {detected}.")

    if score.confirmed:
        confirmed = ", ".join(
            f"{f.factor.value.lower().replace('_', ' ')} (+{f.awarded})"
            for f in score.confirmed
        )
        parts.append(f"Confirmed: {confirmed}.")

    if score.discarded:
        missing = ", ".join(
            f"{f.factor.value.lower().replace('_', ' ')} ({f.condition})"
            for f in score.discarded
        )
        parts.append(f"Missing: {missing}.")

    if score.is_vetoed:
        blocked = ", ".join(
            f"{v.veto.value.lower().replace('_', ' ')} ({v.reason})"
            for v in score.blocking_vetoes
        )
        parts.append(f"Blocked by: {blocked}.")

    # The verdict follows the action actually taken, not the score alone. A
    # setup can clear the threshold and still not trade -- risk has the last
    # word -- and an explanation that claimed otherwise would be lying.
    if record.action == "no_trade":
        verdict = (
            "Not traded; kept on the watchlist."
            if score.conviction is Conviction.WATCHLIST
            else "Not traded."
        )
        if score.tradeable:
            verdict = "Not traded despite clearing the threshold."
    elif score.conviction is Conviction.HIGH:
        verdict = "Entered with high conviction."
    else:
        verdict = "Entered."
    parts.append(f"{verdict} Reason: {record.final_reason}.")

    return " ".join(parts)
