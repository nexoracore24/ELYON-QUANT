"""Risk budget with atomic reservation.

Implements ADR-0007 / the Risk Budget Concurrency Standard.

The bug this exists to prevent: two signals fire at once, both read "there is
budget left", both get approved, and together they breach a limit. Checking and
then consuming is two steps, and anything can happen in between.

So budget is not consulted -- it is *reserved*. Asking for budget atomically
takes it, and every dimension is checked and taken in one indivisible step, so
a concurrent request sees it already gone. Because all of an account's
dimensions live in one aggregate, that step needs no multi-lock dance and
cannot deadlock.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from typing import Final, Iterable, Mapping

from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec, dsum

DEFAULT_RESERVATION_TTL_NS: Final[int] = 300 * 1_000_000_000  # 5 min


class Dimension(str, Enum):
    """The axes a single trade consumes budget along, all at once."""

    DAILY_LOSS = "DAILY_LOSS"
    WEEKLY_LOSS = "WEEKLY_LOSS"
    MONTHLY_LOSS = "MONTHLY_LOSS"
    TOTAL_OPEN_RISK = "TOTAL_OPEN_RISK"
    SYMBOL_RISK = "SYMBOL_RISK"
    STRATEGY_RISK = "STRATEGY_RISK"
    SESSION_RISK = "SESSION_RISK"
    CORRELATION_RISK = "CORRELATION_RISK"


class ReservationState(str, Enum):
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self is not ReservationState.PENDING


class DenialReason(str, Enum):
    INSUFFICIENT_BUDGET = "INSUFFICIENT_BUDGET"
    ENGINE_HALTED = "ENGINE_HALTED"
    UNKNOWN_DIMENSION = "UNKNOWN_DIMENSION"


class RiskError(Exception):
    """A misuse of the budget API, as opposed to a legitimate denial."""


@dataclass(frozen=True, slots=True)
class Reservation:
    """A claim on budget, held until the trade resolves or the claim expires."""

    reservation_id: str
    intent_id: str
    amounts: Mapping[Dimension, Decimal]
    expires_at_ns: int
    state: ReservationState = ReservationState.PENDING

    def total_for(self, dimension: Dimension) -> Decimal:
        return self.amounts.get(dimension, ZERO)


@dataclass(frozen=True, slots=True)
class ReservationResult:
    """Outcome of asking for budget. Denials carry their reason."""

    granted: bool
    reservation: Reservation | None = None
    reason: DenialReason | None = None
    breached: tuple[Dimension, ...] = ()

    @property
    def denied(self) -> bool:
        return not self.granted


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    """A read-only view of one dimension."""

    dimension: Dimension
    total: Decimal
    reserved: Decimal
    committed: Decimal

    @property
    def available(self) -> Decimal:
        return self.total - self.reserved - self.committed


class RiskBudget:
    """Per-account budget aggregate.

    Contention is scoped to one account: separate accounts never coordinate, so
    the design scales horizontally. The ``version`` counter is what makes the
    check-and-take atomic under optimistic concurrency -- a caller that read an
    older version loses the race and retries against the reduced availability.
    """

    def __init__(
        self,
        account_id: str,
        totals: Mapping[Dimension, Decimal],
        *,
        reservation_ttl_ns: int = DEFAULT_RESERVATION_TTL_NS,
    ) -> None:
        if not totals:
            raise RiskError("a budget needs at least one dimension")
        for dimension, total in totals.items():
            if total < ZERO:
                raise RiskError(f"{dimension.value} total cannot be negative: {total}")

        self._account_id = account_id
        self._totals = dict(totals)
        self._reserved: dict[Dimension, Decimal] = {d: ZERO for d in totals}
        self._committed: dict[Dimension, Decimal] = {d: ZERO for d in totals}
        self._reservations: dict[str, Reservation] = {}
        self._by_intent: dict[str, str] = {}
        self._ttl_ns = reservation_ttl_ns
        self._version = 0
        self._halted = False

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def version(self) -> int:
        """Bumped on every mutation -- the basis for compare-and-swap."""
        return self._version

    @property
    def halted(self) -> bool:
        return self._halted

    def halt(self) -> None:
        """Kill switch: stop granting budget. Open positions are unaffected."""
        self._halted = True
        self._version += 1

    def resume(self) -> None:
        self._halted = False
        self._version += 1

    def snapshot(self, dimension: Dimension) -> BudgetSnapshot:
        self._require_known(dimension)
        return BudgetSnapshot(
            dimension,
            self._totals[dimension],
            self._reserved[dimension],
            self._committed[dimension],
        )

    def available(self, dimension: Dimension) -> Decimal:
        return self.snapshot(dimension).available

    def reservation(self, reservation_id: str) -> Reservation | None:
        return self._reservations.get(reservation_id)

    def active_reservations(self) -> list[Reservation]:
        return [
            r for r in self._reservations.values()
            if r.state is ReservationState.PENDING
        ]

    def reserve(
        self,
        *,
        intent_id: str,
        amounts: Mapping[Dimension, Decimal],
        now_ns: int,
        expected_version: int | None = None,
    ) -> ReservationResult:
        """Check and take budget in one indivisible step.

        ``expected_version`` is the compare-and-swap guard: pass the version you
        read and the call fails if anything changed underneath you. Retrying
        then re-reads the *reduced* availability, which is precisely why two
        concurrent callers cannot both spend the same budget.

        Idempotent by ``intent_id``: a retried request returns the reservation
        it already holds instead of taking budget twice.
        """
        if expected_version is not None and expected_version != self._version:
            raise StaleVersionError(
                f"budget moved from version {expected_version} to {self._version}; "
                "re-read and retry"
            )

        existing_id = self._by_intent.get(intent_id)
        if existing_id is not None:
            existing = self._reservations[existing_id]
            if existing.state is ReservationState.PENDING:
                return ReservationResult(granted=True, reservation=existing)

        if self._halted:
            return ReservationResult(granted=False, reason=DenialReason.ENGINE_HALTED)

        requested = {d: a for d, a in amounts.items() if a != ZERO}
        for dimension, amount in requested.items():
            if dimension not in self._totals:
                return ReservationResult(
                    granted=False,
                    reason=DenialReason.UNKNOWN_DIMENSION,
                    breached=(dimension,),
                )
            if amount < ZERO:
                raise RiskError(f"cannot reserve a negative amount: {amount}")

        # All or nothing: if any dimension cannot take it, none of them do.
        breached = tuple(
            d for d, amount in requested.items() if self.available(d) < amount
        )
        if breached:
            return ReservationResult(
                granted=False,
                reason=DenialReason.INSUFFICIENT_BUDGET,
                breached=breached,
            )

        for dimension, amount in requested.items():
            self._reserved[dimension] += amount

        reservation = Reservation(
            reservation_id=f"{self._account_id}:{intent_id}",
            intent_id=intent_id,
            amounts=dict(requested),
            expires_at_ns=now_ns + self._ttl_ns,
        )
        self._reservations[reservation.reservation_id] = reservation
        self._by_intent[intent_id] = reservation.reservation_id
        self._version += 1
        return ReservationResult(granted=True, reservation=reservation)

    def commit(
        self, reservation_id: str, actual: Mapping[Dimension, Decimal] | None = None
    ) -> Reservation:
        """Convert a reservation into committed risk once the order is live.

        ``actual`` lets a partial fill commit less than was reserved; the
        remainder returns to availability rather than being quietly held.
        """
        reservation = self._pending(reservation_id, "commit")
        final = dict(actual) if actual is not None else dict(reservation.amounts)

        for dimension, reserved_amount in reservation.amounts.items():
            committed_amount = final.get(dimension, ZERO)
            if committed_amount > reserved_amount:
                raise RiskError(
                    f"cannot commit {committed_amount} against a reservation of "
                    f"{reserved_amount} on {dimension.value}"
                )
            self._reserved[dimension] -= reserved_amount
            self._committed[dimension] += committed_amount

        return self._settle(reservation, ReservationState.COMMITTED)

    def release(self, reservation_id: str) -> Reservation:
        """Hand budget back -- the order was rejected, cancelled or never filled."""
        reservation = self._pending(reservation_id, "release")
        for dimension, amount in reservation.amounts.items():
            self._reserved[dimension] -= amount
        return self._settle(reservation, ReservationState.RELEASED)

    def expire_due(self, now_ns: int) -> list[Reservation]:
        """Reclaim reservations whose time ran out.

        The safety net against leaks: without it, one lost acknowledgement
        would strand budget forever and slowly starve the account.
        """
        due = [
            r for r in self._reservations.values()
            if r.state is ReservationState.PENDING and now_ns >= r.expires_at_ns
        ]
        expired = []
        for reservation in sorted(due, key=lambda r: r.reservation_id):
            for dimension, amount in reservation.amounts.items():
                self._reserved[dimension] -= amount
            expired.append(self._settle(reservation, ReservationState.EXPIRED))
        return expired

    def release_committed(self, amounts: Mapping[Dimension, Decimal]) -> None:
        """Give back committed risk when a position closes."""
        for dimension, amount in amounts.items():
            self._require_known(dimension)
            if amount > self._committed[dimension]:
                raise RiskError(
                    f"cannot release {amount} of committed risk on "
                    f"{dimension.value}; only {self._committed[dimension]} is held"
                )
            self._committed[dimension] -= amount
        self._version += 1

    def check_invariant(self) -> None:
        """Assert the one rule that must never break, on any dimension."""
        for dimension in self._totals:
            snap = self.snapshot(dimension)
            if snap.reserved + snap.committed > snap.total:
                raise RiskError(
                    f"budget invariant violated on {dimension.value}: "
                    f"reserved {snap.reserved} + committed {snap.committed} "
                    f"exceeds total {snap.total}"
                )

    def _pending(self, reservation_id: str, action: str) -> Reservation:
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            raise RiskError(f"cannot {action} unknown reservation {reservation_id}")
        if reservation.state.is_terminal:
            raise RiskError(
                f"cannot {action} reservation {reservation_id}: already "
                f"{reservation.state.value}"
            )
        return reservation

    def _settle(self, reservation: Reservation, state: ReservationState) -> Reservation:
        settled = replace(reservation, state=state)
        self._reservations[settled.reservation_id] = settled
        self._by_intent.pop(settled.intent_id, None)
        self._version += 1
        return settled

    def _require_known(self, dimension: Dimension) -> None:
        if dimension not in self._totals:
            raise RiskError(f"{dimension.value} is not a dimension of this budget")


class StaleVersionError(RiskError):
    """The compare-and-swap guard fired: re-read the budget and retry."""


def total_exposure(reservations: Iterable[Reservation], dimension: Dimension) -> Decimal:
    """Sum pending claims on one dimension, in a fixed order."""
    pending = sorted(
        (r for r in reservations if r.state is ReservationState.PENDING),
        key=lambda r: r.reservation_id,
    )
    return dsum(r.total_for(dimension) for r in pending)
