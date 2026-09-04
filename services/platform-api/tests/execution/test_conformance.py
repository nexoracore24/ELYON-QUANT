"""Conformance-suite tests, and the partial-fill remainder.

The conformance suite is the thing that makes connecting a real venue safe:
three methods, and getting any of them subtly wrong means duplicate positions
that will not appear in testing. So the suite itself has to be tested against
adapters that are wrong in exactly those ways -- a suite that passes everything
is worse than none, because it grants confidence it did not check.
"""

from __future__ import annotations

import pytest

from elyon.modules.execution.domain import (
    BrokerAck,
    BrokerError,
    BrokerErrorKind,
    BrokerOrderState,
    EventKind,
    IllegalTransition,
    ManualClock,
    Oms,
    OrderRequest,
    OrderState,
    PaperBroker,
    Side,
    check_adapter,
    client_order_id,
    unavailable,
)
from elyon.shared_kernel.edcs.numeric import ZERO, dec

QTY = dec("0.10")


def oms_with(**kwargs):
    clock = ManualClock()
    broker = PaperBroker(clock, **kwargs)
    return Oms(broker, clock), broker, clock


def queued(oms, tag="d-1") -> str:
    coid = client_order_id(tag)
    oms.create(OrderRequest(coid, tag, "EURUSD", Side.BUY, QTY))
    oms.validate(coid)
    oms.approve_risk(coid)
    oms.queue(coid)
    return coid


# ---------------------------------------------------------------------------
# The conformance suite
# ---------------------------------------------------------------------------

class TestAConformingAdapterPasses:
    def test_the_paper_broker_conforms(self):
        report = check_adapter(lambda clock: PaperBroker(clock), ManualClock())
        assert report.safe_to_use, str(report)

    def test_every_check_reports_a_detail(self):
        report = check_adapter(lambda clock: PaperBroker(clock), ManualClock())
        for check in report.checks:
            assert check.detail.strip()

    def test_the_summary_says_it_is_safe(self):
        report = check_adapter(lambda clock: PaperBroker(clock), ManualClock())
        assert "guarantees hold" in str(report)


class TestTheSuiteCatchesTheDangerousMistakes:
    """A suite that passes everything grants confidence it did not check."""

    def test_an_adapter_that_claims_unknown_orders_exist_fails(self):
        # The OMS would adopt a phantom and the real order would never be sent.
        class Phantom(PaperBroker):
            def query(self, client_order_id):
                return BrokerOrderState(exists=True, broker_order_id="B-?")

        report = check_adapter(lambda clock: Phantom(clock), ManualClock())
        assert not report.safe_to_use
        assert any("phantom" in c.detail for c in report.failures)

    def test_an_adapter_that_forgets_its_own_orders_fails(self):
        # After a timeout the OMS would conclude nothing was placed and send a
        # second order. This is the duplicate-position bug.
        class Amnesiac(PaperBroker):
            def query(self, client_order_id):
                return BrokerOrderState(exists=False)

        report = check_adapter(lambda clock: Amnesiac(clock), ManualClock())
        assert not report.safe_to_use
        assert any("duplicate-position" in c.detail for c in report.failures)

    def test_an_adapter_that_does_not_deduplicate_fails(self):
        class Careless(PaperBroker):
            _n = 0

            def place(self, request, idempotency_key):
                Careless._n += 1
                self._register(request)
                return BrokerAck(f"B-{Careless._n}", self.clock.now_ns())

        report = check_adapter(lambda clock: Careless(clock), ManualClock())
        assert any(
            "double the position" in c.detail for c in report.failures
        )

    def test_an_adapter_that_raises_bare_exceptions_fails(self):
        # The OMS cannot tell a rejection from a timeout, so it will not
        # reconcile -- and reconciling is the whole safety mechanism.
        class Untyped(PaperBroker):
            def place(self, request, idempotency_key):
                raise RuntimeError("something went wrong")

        report = check_adapter(lambda clock: Untyped(clock), ManualClock())
        assert not report.safe_to_use
        assert any("rather than BrokerError" in c.detail for c in report.checks)

    def test_an_adapter_that_cannot_answer_query_is_flagged(self):
        class Mute(PaperBroker):
            def query(self, client_order_id):
                raise unavailable()

        report = check_adapter(lambda clock: Mute(clock), ManualClock())
        assert not report.safe_to_use
        assert any("never recover" in c.detail for c in report.failures)

    def test_typing_a_rejection_as_a_timeout_fails(self):
        # The OMS would reconcile over a refusal instead of recording it, and
        # keep retrying an order the venue will never accept.
        class Miscategorised(PaperBroker):
            def place(self, request, idempotency_key):
                if "NO_SUCH" in request.symbol:
                    raise BrokerError(BrokerErrorKind.TIMEOUT, "unknown symbol")
                return super().place(request, idempotency_key)

        report = check_adapter(
            lambda clock: Miscategorised(clock), ManualClock()
        )
        assert not report.safe_to_use
        assert any("outcome unknown" in c.detail for c in report.failures)

    def test_a_rejection_typed_correctly_passes(self):
        class Correct(PaperBroker):
            def place(self, request, idempotency_key):
                if "NO_SUCH" in request.symbol:
                    raise BrokerError(BrokerErrorKind.INVALID, "unknown symbol")
                return super().place(request, idempotency_key)

        report = check_adapter(lambda clock: Correct(clock), ManualClock())
        assert report.safe_to_use

    def test_an_adapter_that_cannot_be_provoked_says_so(self):
        # Saying "passed" here would grant confidence the suite did not earn.
        report = check_adapter(lambda clock: PaperBroker(clock), ManualClock())
        typed = next(c for c in report.checks if c.name == "errors are typed")
        assert "could not provoke" in typed.detail
        assert not typed.critical

    def test_a_non_critical_failure_does_not_block_use(self):
        # Cancel visibility matters, but it cannot duplicate a position.
        class NoCancel(PaperBroker):
            def cancel(self, client_order_id):
                pass   # silently does nothing

        report = check_adapter(lambda clock: NoCancel(clock), ManualClock())
        assert report.safe_to_use
        assert any(not c.passed and not c.critical for c in report.checks)


# ---------------------------------------------------------------------------
# Withdrawing the remainder of a partial fill
# ---------------------------------------------------------------------------

class TestCancellingIsNotWithdrawing:
    def test_an_order_holding_a_position_cannot_be_cancelled(self):
        # Cancelling would seal the outcome of a trade that has not finished,
        # and the live position would be lost with it.
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", dec("0.04"), dec("1.1000"))

        with pytest.raises(IllegalTransition, match="discard a live position"):
            oms.cancel(coid)

    def test_the_refusal_points_at_the_right_operation(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", dec("0.04"), dec("1.1000"))

        with pytest.raises(IllegalTransition, match="cancel_remainder"):
            oms.cancel(coid)

    def test_an_unfilled_order_cancels_normally(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        assert oms.cancel(coid).state is OrderState.CANCELLED

    def test_withdrawing_the_remainder_completes_the_order(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", dec("0.04"), dec("1.1000"))

        order = oms.cancel_remainder(coid)

        assert order.is_complete
        assert order.remaining_quantity == ZERO
        assert order.cancelled_quantity == dec("0.06")

    def test_it_does_not_pretend_the_remainder_filled(self):
        # The order is complete, but only 0.04 was ever bought. Reporting 0.10
        # would misstate the position and every risk figure derived from it.
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", dec("0.04"), dec("1.1000"))
        order = oms.cancel_remainder(coid)

        assert order.filled_quantity == dec("0.04")
        assert not order.is_fully_filled

    def test_the_outcome_is_not_sealed(self):
        # It still holds a position, and a position is not an outcome. Only
        # closing it is.
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", dec("0.04"), dec("1.1000"))
        order = oms.cancel_remainder(coid)

        assert not order.state.is_terminal
        assert order.state.has_exposure

    def test_it_is_a_distinct_event(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", dec("0.04"), dec("1.1000"))
        oms.cancel_remainder(coid)

        kinds = [e.kind for e in oms.log_of(coid)]
        assert EventKind.REMAINDER_CANCELLED in kinds
        assert EventKind.CANCELLED not in kinds

    def test_there_is_nothing_to_withdraw_on_an_unfilled_order(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        with pytest.raises(IllegalTransition, match="no partial fill"):
            oms.cancel_remainder(coid)

    def test_a_refused_cancel_is_not_recorded_as_done(self):
        # The venue may still fill it. Recording a withdrawal we could not
        # achieve would leave the OMS blind to a fill that then arrives.
        class Stubborn(PaperBroker):
            def cancel(self, client_order_id):
                raise unavailable()

        clock = ManualClock()
        broker = Stubborn(clock)
        oms = Oms(broker, clock)
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", dec("0.04"), dec("1.1000"))

        order = oms.cancel_remainder(coid)

        assert order.cancelled_quantity == ZERO
        assert order.remaining_quantity == dec("0.06")
        assert len(oms.dlq) == 1
        assert "may still fill" in oms.dlq.entries[0].reason
