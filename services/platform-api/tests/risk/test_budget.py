"""Risk budget and sizing tests.

The headline case is the concurrency one: two signals firing at the same moment
must not both spend the same budget. Everything else here protects the capital
in less dramatic but equally final ways.
"""

from __future__ import annotations

import random

import pytest

from elyon.modules.risk.domain.budget import (
    DenialReason,
    Dimension,
    ReservationState,
    RiskBudget,
    RiskError,
    StaleVersionError,
    total_exposure,
)
from elyon.modules.risk.domain.sizing import (
    InstrumentSpec,
    SizingRejection,
    SizingRequest,
    reward_to_risk,
    scale_risk,
    size_position,
)
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

DAILY = Dimension.DAILY_LOSS
OPEN = Dimension.TOTAL_OPEN_RISK
SYMBOL_RISK = Dimension.SYMBOL_RISK
NOW = 1_000_000_000_000


def budget(**totals: str) -> RiskBudget:
    mapping = {Dimension[k]: dec(v) for k, v in totals.items()}
    return RiskBudget("acct-1", mapping)


class TestReservation:
    def test_budget_is_taken_the_moment_it_is_reserved(self):
        b = budget(DAILY_LOSS="300")
        result = b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW)

        assert result.granted
        # The next caller already sees it gone -- that is the whole point.
        assert b.available(DAILY) == dec("250")

    def test_a_request_beyond_the_budget_is_denied(self):
        b = budget(DAILY_LOSS="100")
        result = b.reserve(intent_id="i1", amounts={DAILY: dec("150")}, now_ns=NOW)

        assert result.denied
        assert result.reason is DenialReason.INSUFFICIENT_BUDGET
        assert result.breached == (DAILY,)
        assert b.available(DAILY) == dec("100")  # nothing taken

    def test_a_reservation_is_all_or_nothing_across_dimensions(self):
        # Room on the daily axis, none on the symbol axis: neither is touched.
        b = budget(DAILY_LOSS="300", SYMBOL_RISK="20")
        result = b.reserve(
            intent_id="i1",
            amounts={DAILY: dec("50"), SYMBOL_RISK: dec("50")},
            now_ns=NOW,
        )

        assert result.denied
        assert result.breached == (SYMBOL_RISK,)
        assert b.available(DAILY) == dec("300")

    def test_reserving_twice_for_one_intent_takes_budget_once(self):
        b = budget(DAILY_LOSS="300")
        first = b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW)
        second = b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW)

        assert second.granted
        assert second.reservation == first.reservation
        assert b.available(DAILY) == dec("250")

    def test_an_unknown_dimension_is_refused(self):
        b = budget(DAILY_LOSS="300")
        result = b.reserve(
            intent_id="i1", amounts={Dimension.WEEKLY_LOSS: dec("10")}, now_ns=NOW
        )
        assert result.denied and result.reason is DenialReason.UNKNOWN_DIMENSION

    def test_a_halted_engine_grants_nothing(self):
        b = budget(DAILY_LOSS="300")
        b.halt()
        result = b.reserve(intent_id="i1", amounts={DAILY: dec("10")}, now_ns=NOW)
        assert result.denied and result.reason is DenialReason.ENGINE_HALTED

        b.resume()
        assert b.reserve(intent_id="i2", amounts={DAILY: dec("10")}, now_ns=NOW).granted


class TestConcurrency:
    """The race the whole design exists to eliminate."""

    def test_two_concurrent_requests_cannot_both_spend_the_same_budget(self):
        # Room for exactly one 60-unit trade.
        b = budget(DAILY_LOSS="100")
        version = b.version

        first = b.reserve(
            intent_id="a", amounts={DAILY: dec("60")}, now_ns=NOW,
            expected_version=version,
        )
        assert first.granted

        # The second caller read the same version: its compare-and-swap fails.
        with pytest.raises(StaleVersionError):
            b.reserve(
                intent_id="b", amounts={DAILY: dec("60")}, now_ns=NOW,
                expected_version=version,
            )

        # Retrying against the current state sees the reduced availability.
        retry = b.reserve(
            intent_id="b", amounts={DAILY: dec("60")}, now_ns=NOW,
            expected_version=b.version,
        )
        assert retry.denied
        b.check_invariant()

    def test_the_version_advances_on_every_mutation(self):
        b = budget(DAILY_LOSS="300")
        start = b.version
        b.reserve(intent_id="i1", amounts={DAILY: dec("10")}, now_ns=NOW)
        assert b.version > start

    def test_the_invariant_holds_under_random_interleaving(self):
        rng = random.Random(20260729)
        b = budget(DAILY_LOSS="1000", TOTAL_OPEN_RISK="500")

        for step in range(400):
            action = rng.choice(["reserve", "commit", "release", "expire"])
            if action == "reserve":
                b.reserve(
                    intent_id=f"i{step}",
                    amounts={
                        DAILY: dec(str(rng.randint(1, 80))),
                        OPEN: dec(str(rng.randint(1, 40))),
                    },
                    now_ns=NOW + step,
                )
            elif action in ("commit", "release"):
                active = b.active_reservations()
                if active:
                    target = rng.choice(active).reservation_id
                    (b.commit if action == "commit" else b.release)(target)
            else:
                b.expire_due(NOW + step + 10**12)
            b.check_invariant()  # must hold after every single step


class TestLifecycle:
    def test_committing_moves_budget_from_reserved_to_committed(self):
        b = budget(DAILY_LOSS="300")
        r = b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW).reservation
        assert r is not None

        committed = b.commit(r.reservation_id)
        snap = b.snapshot(DAILY)

        assert committed.state is ReservationState.COMMITTED
        assert snap.reserved == dec("0")
        assert snap.committed == dec("50")
        assert snap.available == dec("250")

    def test_a_partial_fill_commits_less_and_returns_the_rest(self):
        b = budget(DAILY_LOSS="300")
        r = b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW).reservation
        assert r is not None

        b.commit(r.reservation_id, {DAILY: dec("30")})
        snap = b.snapshot(DAILY)

        assert snap.committed == dec("30")
        assert snap.available == dec("270")  # the unused 20 came back

    def test_committing_more_than_was_reserved_is_refused(self):
        b = budget(DAILY_LOSS="300")
        r = b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW).reservation
        assert r is not None
        with pytest.raises(RiskError, match="cannot commit"):
            b.commit(r.reservation_id, {DAILY: dec("80")})

    def test_releasing_returns_the_budget(self):
        b = budget(DAILY_LOSS="300")
        r = b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW).reservation
        assert r is not None
        b.release(r.reservation_id)
        assert b.available(DAILY) == dec("300")

    def test_a_settled_reservation_cannot_be_settled_again(self):
        b = budget(DAILY_LOSS="300")
        r = b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW).reservation
        assert r is not None
        b.commit(r.reservation_id)
        with pytest.raises(RiskError, match="already COMMITTED"):
            b.release(r.reservation_id)

    def test_closing_a_position_frees_committed_risk(self):
        b = budget(DAILY_LOSS="300")
        r = b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW).reservation
        assert r is not None
        b.commit(r.reservation_id)
        b.release_committed({DAILY: dec("50")})
        assert b.available(DAILY) == dec("300")


class TestExpiry:
    def test_a_stale_reservation_is_reclaimed(self):
        # Without this, one lost acknowledgement strands budget forever.
        b = budget(DAILY_LOSS="300")
        b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW)

        assert b.expire_due(NOW + 10) == []          # still inside its TTL
        expired = b.expire_due(NOW + 10**12)

        assert len(expired) == 1
        assert expired[0].state is ReservationState.EXPIRED
        assert b.available(DAILY) == dec("300")

    def test_expiry_is_idempotent(self):
        b = budget(DAILY_LOSS="300")
        b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW)
        b.expire_due(NOW + 10**12)
        assert b.expire_due(NOW + 10**12) == []

    def test_an_expired_intent_can_be_reserved_afresh(self):
        b = budget(DAILY_LOSS="300")
        b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW)
        b.expire_due(NOW + 10**12)
        assert b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW).granted

    def test_exposure_counts_only_pending_claims(self):
        b = budget(DAILY_LOSS="300")
        b.reserve(intent_id="i1", amounts={DAILY: dec("50")}, now_ns=NOW)
        r2 = b.reserve(intent_id="i2", amounts={DAILY: dec("30")}, now_ns=NOW).reservation
        assert r2 is not None
        b.release(r2.reservation_id)
        assert total_exposure(b.active_reservations(), DAILY) == dec("50")


class TestSizing:
    SPEC = InstrumentSpec(
        lot_step=dec("0.01"), min_lot=dec("0.01"),
        max_lot=dec("100"), value_per_price_unit=dec("100000"),
    )

    def _request(self, **overrides) -> SizingRequest:
        base = dict(
            equity=dec("10000"), risk_fraction=dec("0.005"),
            entry=dec("1.1000"), stop_loss=dec("1.0980"),
            take_profit=dec("1.1060"), spec=self.SPEC,
        )
        base.update(overrides)
        return SizingRequest(**base)  # type: ignore[arg-type]

    def test_size_follows_from_the_stop(self):
        # 0.5% of 10k = 50 risked; a 20-pip stop at 10/pip/lot -> 0.25 lots.
        result = size_position(self._request())
        assert result.approved
        assert result.lots == dec("0.25")
        assert result.risk_amount == dec("50.00000")

    def test_lots_round_down_never_up(self):
        # An awkward stop must never round into more risk than approved.
        result = size_position(self._request(stop_loss=dec("1.09873")))
        assert result.approved
        assert result.risk_amount <= dec("50")

    def test_a_zero_stop_is_rejected(self):
        result = size_position(self._request(stop_loss=dec("1.1000")))
        assert result.rejected and result.rejection is SizingRejection.INVALID_STOP

    def test_a_poor_reward_to_risk_is_rejected(self):
        result = size_position(
            self._request(take_profit=dec("1.1020")), min_reward_risk=dec("2")
        )
        assert result.rejected and result.rejection is SizingRejection.RR_BELOW_MINIMUM
        assert result.reward_risk == dec("1")

    def test_an_over_wide_stop_is_rejected(self):
        result = size_position(
            self._request(stop_loss=dec("1.0900")),
            max_stop_atr=dec("3"), atr=dec("0.0010"),
        )
        assert result.rejected and result.rejection is SizingRejection.STOP_TOO_WIDE

    def test_a_position_too_small_to_trade_is_refused_not_rounded_up(self):
        result = size_position(
            self._request(equity=dec("10"), stop_loss=dec("1.0000"))
        )
        assert result.rejected and result.rejection is SizingRejection.BELOW_MIN_LOT

    def test_size_is_capped_by_the_broker_maximum(self):
        spec = InstrumentSpec(
            lot_step=dec("0.01"), min_lot=dec("0.01"),
            max_lot=dec("0.10"), value_per_price_unit=dec("100000"),
        )
        result = size_position(self._request(equity=dec("1000000"), spec=spec))
        assert result.approved and result.lots == dec("0.10")

    def test_reward_to_risk_needs_a_real_stop(self):
        with pytest.raises(DeterminismError):
            reward_to_risk(dec("1.1"), dec("1.1"), dec("1.2"))

    def test_sizing_is_reproducible(self):
        assert size_position(self._request()) == size_position(self._request())


class TestDynamicRisk:
    def test_multipliers_lean_the_size(self):
        scaled = scale_risk(
            dec("0.005"), multipliers=[dec("1.2")],
            floor=dec("0.001"), ceiling=dec("0.01"),
        )
        assert scaled == dec("0.006")

    def test_no_combination_of_signals_can_breach_the_ceiling(self):
        # Conviction is allowed to argue; it is not allowed to win.
        scaled = scale_risk(
            dec("0.005"), multipliers=[dec("2"), dec("2"), dec("2")],
            floor=dec("0.001"), ceiling=dec("0.01"),
        )
        assert scaled == dec("0.01")

    def test_the_floor_holds_too(self):
        scaled = scale_risk(
            dec("0.005"), multipliers=[dec("0.01")],
            floor=dec("0.001"), ceiling=dec("0.01"),
        )
        assert scaled == dec("0.001")

    def test_a_negative_multiplier_is_a_bug_not_a_hedge(self):
        with pytest.raises(DeterminismError):
            scale_risk(
                dec("0.005"), multipliers=[dec("-1")],
                floor=dec("0.001"), ceiling=dec("0.01"),
            )
