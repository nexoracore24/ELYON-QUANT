"""OMS tests.

The expensive bug an OMS can have is a duplicate position: it doubles risk
silently, and the account looks fine until it does not. Nearly everything here
is ultimately about preventing that, from three directions.

    1. The state machine -- QUEUED is the only state that reaches SENT.
    2. The client order id -- deterministic, so a retry is recognisably the
       same order rather than a new one.
    3. Query-before-resend -- a timed-out send has an *unknown* outcome, and
       the OMS asks rather than guesses.

The rest is what happens when the answer is still unknown: stop sending, protect
what is open, and never invent a state the broker did not confirm.
"""

from __future__ import annotations

import pytest

from elyon.modules.execution.domain import (
    TERMINAL_STATES,
    TRANSITIONS,
    BreakerState,
    BrokerError,
    BrokerErrorKind,
    BrokerOrderState,
    CircuitBreaker,
    DeadLetterQueue,
    EventKind,
    IllegalTransition,
    ManualClock,
    Oms,
    OmsConfig,
    Order,
    OrderEvent,
    OrderRequest,
    OrderState,
    OrderType,
    Outbox,
    PaperBroker,
    Side,
    client_order_id,
    idempotency_key,
    rejection,
    timeout,
    unavailable,
)
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec

SECOND = 1_000_000_000
SYMBOL = "EURUSD"
QTY = dec("0.10")


def request(correlation: str = "decision-1", **kwargs) -> OrderRequest:
    coid = kwargs.pop("client_order_id", client_order_id(correlation))
    settings = dict(
        client_order_id=coid, correlation_id=correlation, symbol=SYMBOL,
        side=Side.BUY, quantity=QTY,
    )
    settings.update(kwargs)
    return OrderRequest(**settings)


def oms_with(**broker_kwargs) -> tuple[Oms, PaperBroker, ManualClock]:
    clock = ManualClock()
    broker = PaperBroker(clock, **broker_kwargs)
    return Oms(broker, clock), broker, clock


def queued(oms: Oms, req: OrderRequest | None = None) -> str:
    req = req or request()
    oms.create(req)
    oms.validate(req.client_order_id)
    oms.approve_risk(req.client_order_id)
    oms.queue(req.client_order_id)
    return req.client_order_id


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------

class TestTheStateMachine:
    def test_only_queued_reaches_sent(self):
        # The single source state is what makes a duplicate send impossible
        # rather than merely discouraged.
        assert set(TRANSITIONS[EventKind.SENT]) == {OrderState.QUEUED}

    def test_no_terminal_state_transitions_back_to_active(self):
        # Once an outcome is sealed it cannot be reopened, or the log stops
        # being an account of what happened.
        for kind, table in TRANSITIONS.items():
            if kind is EventKind.ARCHIVED:
                continue
            for source in table:
                assert source not in TERMINAL_STATES, (kind, source)

    def test_risk_approval_cannot_be_skipped(self):
        # A blocking contract with ENG-005: there is no path from VALIDATED to
        # QUEUED that does not pass through it.
        assert set(TRANSITIONS[EventKind.QUEUED]) == {OrderState.RISK_APPROVED}

    def test_an_event_in_the_wrong_state_is_refused(self):
        oms, _, _ = oms_with()
        req = request()
        oms.create(req)
        with pytest.raises(IllegalTransition, match="cannot apply"):
            oms.queue(req.client_order_id)   # never validated, never approved

    def test_a_terminal_order_refuses_further_events(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.cancel(coid, "changed mind")
        with pytest.raises(IllegalTransition, match="terminal"):
            oms.order(coid).apply(OrderEvent(EventKind.SENT, coid, at_ns=0))

    def test_exposure_is_recognised(self):
        assert OrderState.FILLED.has_exposure
        assert OrderState.PARTIALLY_FILLED.has_exposure
        assert not OrderState.QUEUED.has_exposure

    def test_states_the_broker_may_still_act_on_are_recognised(self):
        # The set reconciliation has to cover: a fill can still arrive here.
        assert OrderState.SENT.is_live_at_broker
        assert OrderState.ACKNOWLEDGED.is_live_at_broker
        assert OrderState.RECOVERY.is_live_at_broker
        assert not OrderState.QUEUED.is_live_at_broker


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

class TestDeterministicIdentity:
    def test_the_same_decision_yields_the_same_order_id(self):
        # A retry has to be recognisably the same order. An id from a clock or
        # a random source would make every retry look brand new to the broker,
        # which is precisely how duplicate positions happen.
        assert client_order_id("decision-1") == client_order_id("decision-1")

    def test_different_decisions_yield_different_ids(self):
        assert client_order_id("decision-1") != client_order_id("decision-2")

    def test_legs_of_one_decision_are_distinguishable(self):
        assert client_order_id("d", "entry") != client_order_id("d", "stop")

    def test_idempotency_keys_are_per_command(self):
        coid = client_order_id("d")
        assert idempotency_key(coid, "place") != idempotency_key(coid, "cancel")
        assert idempotency_key(coid, "place") == idempotency_key(coid, "place")


# ---------------------------------------------------------------------------
# Event sourcing
# ---------------------------------------------------------------------------

class TestEventSourcing:
    def test_state_is_a_fold_over_the_log(self):
        oms, broker, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", QTY, dec("1.1000"))

        rebuilt = Order.replay(oms.order(coid).request, oms.log_of(coid))
        assert rebuilt.state is oms.order(coid).state
        assert rebuilt.filled_quantity == oms.order(coid).filled_quantity

    def test_replaying_twice_gives_the_same_order(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        req, log = oms.order(coid).request, oms.log_of(coid)
        assert Order.replay(req, log) == Order.replay(req, log)

    def test_a_log_that_does_not_start_with_creation_is_refused(self):
        with pytest.raises(DeterminismError, match="not CREATED"):
            Order.replay(request(), [OrderEvent(EventKind.SENT, "x", at_ns=0)])

    def test_an_empty_log_is_refused(self):
        with pytest.raises(DeterminismError, match="empty event log"):
            Order.replay(request(), [])

    def test_events_are_sequenced_by_the_store(self):
        # An event's position in the log is a property of the log; letting a
        # caller choose it is how two events claim the same slot.
        oms, _, _ = oms_with()
        coid = queued(oms)
        sequences = [e.sequence for e in oms.log_of(coid)]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)

    def test_the_sent_event_is_written_before_the_call(self):
        # If the process dies during the network call, recovery must find a
        # record that something was attempted.
        oms, broker, _ = oms_with(accept_despite_failure=False)
        broker.fail_place = [timeout()]
        coid = queued(oms)
        oms.send(coid)
        kinds = [e.kind for e in oms.log_of(coid)]
        assert EventKind.SENT in kinds
        assert kinds.index(EventKind.SENT) < kinds.index(EventKind.RECOVERY_STARTED)


# ---------------------------------------------------------------------------
# The duplicate-order problem
# ---------------------------------------------------------------------------

class TestNeverTwoPositions:
    """The bug that matters. Every path below places the venue exactly once."""

    def test_a_timeout_with_the_order_already_placed_adopts_it(self):
        # The request landed, the answer did not. Indistinguishable from a
        # failed send -- which is why the OMS asks instead of assuming.
        oms, broker, _ = oms_with(accept_despite_failure=True)
        broker.fail_place = [timeout()]
        coid = queued(oms)

        outcome = oms.send(coid)

        assert outcome.adopted
        assert broker.times_placed(coid) == 1
        assert oms.order(coid).state is OrderState.ACKNOWLEDGED

    def test_a_timeout_with_the_order_absent_resends_under_the_same_id(self):
        oms, broker, _ = oms_with(accept_despite_failure=False)
        broker.fail_place = [timeout()]
        coid = queued(oms)

        outcome = oms.send(coid)

        assert outcome.sent
        assert not outcome.adopted
        assert broker.times_placed(coid) == 1  # the first attempt never landed
        assert oms.order(coid).send_attempts == 2

    def test_the_resend_carries_the_original_id_so_the_venue_can_dedupe(self):
        oms, broker, _ = oms_with(accept_despite_failure=False)
        broker.fail_place = [timeout()]
        coid = queued(oms)
        oms.send(coid)
        assert broker.placed == [coid]

    def test_sending_twice_is_refused_by_the_machine(self):
        oms, broker, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)

        second = oms.send(coid)

        assert not second.sent
        assert "refusing to send twice" in second.reason
        assert broker.times_placed(coid) == 1

    def test_creating_the_same_order_twice_is_one_order(self):
        oms, _, _ = oms_with()
        req = request()
        first = oms.create(req)
        second = oms.create(req)
        assert first.client_order_id == second.client_order_id
        assert len(oms.orders) == 1

    def test_a_venue_that_already_has_the_id_returns_the_same_order(self):
        # Real venues dedupe on client order id; the paper one does too, which
        # is what makes a same-id resend safe rather than a second position.
        oms, broker, clock = oms_with()
        coid = queued(oms)
        oms.send(coid)
        ack = broker.place(oms.order(coid).request, "k")
        assert broker.times_placed(coid) == 1


class TestRejectionIsNotDoubt:
    def test_an_explicit_rejection_is_recorded_not_reconciled(self):
        # A rejection is a fact. Only a timeout is a question.
        oms, broker, _ = oms_with()
        broker.fail_place = [rejection("insufficient margin")]
        coid = queued(oms)

        outcome = oms.send(coid)

        assert not outcome.sent
        assert oms.order(coid).state is OrderState.REJECTED
        assert "insufficient margin" in oms.order(coid).events[-1].reason

    def test_a_rejection_does_not_trigger_recovery(self):
        oms, broker, _ = oms_with()
        broker.fail_place = [rejection()]
        coid = queued(oms)
        oms.send(coid)
        kinds = [e.kind for e in oms.log_of(coid)]
        assert EventKind.RECOVERY_STARTED not in kinds


# ---------------------------------------------------------------------------
# Fills
# ---------------------------------------------------------------------------

class TestFills:
    def test_partial_fills_aggregate(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", dec("0.04"), dec("1.1000"))
        order = oms.on_fill(coid, "F-2", dec("0.06"), dec("1.1002"))

        assert order.filled_quantity == QTY
        assert order.state is OrderState.FILLED

    def test_the_average_price_is_volume_weighted(self):
        # A plain mean of prices misstates the entry, and every risk figure
        # derived from it.
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", dec("0.09"), dec("1.1000"))
        oms.on_fill(coid, "F-2", dec("0.01"), dec("1.2000"))

        average = oms.order(coid).average_fill_price
        assert average == dec("1.11")           # weighted
        assert average != dec("1.15")           # not the plain mean

    def test_a_redelivered_fill_is_applied_once(self):
        # At-least-once delivery plus dedup here is how exactly-once is
        # achieved logically, without needing it from the transport.
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", dec("0.05"), dec("1.1000"))
        oms.on_fill(coid, "F-1", dec("0.05"), dec("1.1000"))   # same event id

        assert oms.order(coid).filled_quantity == dec("0.05")
        assert len(oms.order(coid).fills) == 1

    def test_an_over_fill_halts_the_oms_rather_than_averaging_it_away(self):
        # The broker says we hold more than we asked for. That is a discrepancy
        # a human needs to see, not arithmetic to smooth over.
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", QTY, dec("1.1000"))
        oms.on_fill(coid, "F-2", dec("0.05"), dec("1.1000"))

        assert oms.is_halted
        assert "over-fill" in oms.halt_reason
        assert len(oms.dlq) == 1

    def test_the_over_fill_never_enters_the_log(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", QTY, dec("1.1000"))
        oms.on_fill(coid, "F-2", dec("0.05"), dec("1.1000"))
        assert oms.order(coid).filled_quantity == QTY

    def test_conservation_is_checked_on_the_aggregate(self):
        order = Order.create(request(), at_ns=0)
        assert order.filled_quantity == ZERO
        order.check_conservation()   # must not raise


# ---------------------------------------------------------------------------
# Fail-safe
# ---------------------------------------------------------------------------

class TestFailSafe:
    def test_an_unreachable_broker_during_reconciliation_halts_the_oms(self):
        # Cannot see the broker, cannot know the truth. Guessing here is how a
        # position gets duplicated or abandoned.
        oms, broker, _ = oms_with(accept_despite_failure=True)
        broker.fail_place = [timeout()]
        broker.fail_query = [unavailable()]
        coid = queued(oms)

        outcome = oms.send(coid)

        assert oms.is_halted
        assert not outcome.sent
        assert "cannot reach broker" in oms.halt_reason

    def test_a_halted_oms_sends_nothing_new(self):
        oms, broker, _ = oms_with()
        coid = queued(oms)
        oms.halt("kill switch")
        outcome = oms.send(coid)
        assert not outcome.sent
        assert broker.times_placed(coid) == 0

    def test_halting_does_not_close_open_positions(self):
        # Closing during an outage means trading blind at the worst moment.
        # Halting means taking no *new* risk while things are unclear.
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", QTY, dec("1.1000"))
        oms.halt("circuit breaker")

        assert oms.order(coid).state is OrderState.FILLED
        assert len(oms.exposed_orders()) == 1

    def test_queueing_while_halted_parks_the_order(self):
        oms, _, _ = oms_with()
        req = request()
        oms.create(req)
        oms.validate(req.client_order_id)
        oms.approve_risk(req.client_order_id)
        oms.halt("maintenance")
        order = oms.queue(req.client_order_id)
        assert order.state is OrderState.SAFE_HALT

    def test_resuming_releases_parked_orders(self):
        oms, _, _ = oms_with()
        req = request()
        oms.create(req)
        oms.validate(req.client_order_id)
        oms.approve_risk(req.client_order_id)
        oms.halt("maintenance")
        oms.queue(req.client_order_id)
        oms.resume()
        assert oms.order(req.client_order_id).state is OrderState.QUEUED

    def test_a_halt_without_a_reason_is_refused(self):
        oms, _, _ = oms_with()
        with pytest.raises(DeterminismError, match="cannot be reviewed"):
            oms.halt("   ")


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

class TestRecovery:
    def test_a_restart_reconciles_everything_the_broker_might_hold(self):
        oms, broker, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)

        outcomes = oms.recover()

        assert len(outcomes) == 1
        assert outcomes[0].adopted

    def test_recovery_adopts_fills_the_oms_never_saw(self):
        # The classic post-restart case: it filled while we were away.
        oms, broker, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        broker.fill(coid, QTY, dec("1.1005"))

        oms.recover()

        assert oms.order(coid).filled_quantity == QTY

    def test_recovery_does_not_double_fills_it_already_had(self):
        oms, broker, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        fill = broker.fill(coid, dec("0.05"), dec("1.1000"))
        oms.on_fill(coid, fill.broker_event_id, fill.quantity, fill.price)

        oms.recover()

        assert oms.order(coid).filled_quantity == dec("0.05")

    def test_terminal_orders_are_left_alone(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.cancel(coid)
        assert oms.recover() == []

    def test_the_broker_is_the_authority(self):
        # Where the OMS and the broker disagree, the OMS is wrong by
        # definition: it is not the one holding the position.
        oms, broker, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        broker.orders[coid] = BrokerOrderState(
            exists=True, broker_order_id="B-9", state=OrderState.CANCELLED
        )
        oms.reconcile(coid, trigger="test")
        assert oms.order(coid).state is OrderState.CANCELLED

    def test_retries_are_bounded(self):
        # A venue that will not accept but *will* answer: the OMS may keep
        # trying, and must eventually stop rather than loop forever.
        oms, broker, _ = oms_with(accept_despite_failure=False)
        broker.fail_place = [timeout()] * 12
        coid = queued(oms)

        oms.send(coid)

        order = oms.order(coid)
        assert order.state is OrderState.FAILED
        assert order.send_attempts == oms.config.max_send_retries + 1

    def test_exhausting_retries_still_never_duplicates(self):
        # Four attempts, zero placements. The point of asking first.
        oms, broker, _ = oms_with(accept_despite_failure=False)
        broker.fail_place = [timeout()] * 12
        coid = queued(oms)
        oms.send(coid)
        assert broker.times_placed(coid) == 0

    def test_a_venue_that_goes_dark_mid_retry_halts_instead_of_looping(self):
        oms, broker, _ = oms_with(accept_despite_failure=False)
        broker.fail_place = [timeout()] * 12
        broker.fail_query = [unavailable()]
        coid = queued(oms)
        oms.send(coid)
        assert oms.is_halted


# ---------------------------------------------------------------------------
# Resilience machinery
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_it_opens_after_repeated_failures(self):
        breaker = CircuitBreaker("broker", failure_threshold=3)
        for _ in range(3):
            breaker.record_failure(0)
        assert breaker.is_open
        assert not breaker.allows(0)

    def test_it_probes_after_the_reset_window(self):
        breaker = CircuitBreaker("broker", failure_threshold=1, reset_after_ns=SECOND)
        breaker.record_failure(0)
        assert not breaker.allows(SECOND // 2)
        assert breaker.allows(SECOND)
        assert breaker.state is BreakerState.HALF_OPEN

    def test_a_failed_probe_reopens_immediately(self):
        # The dependency just said it is still broken; sending the remaining
        # probes into a known hole helps nobody.
        breaker = CircuitBreaker("broker", failure_threshold=5, reset_after_ns=SECOND)
        for _ in range(5):
            breaker.record_failure(0)
        breaker.allows(SECOND)
        breaker.record_failure(SECOND)
        assert breaker.is_open

    def test_recovery_needs_more_than_one_lucky_response(self):
        breaker = CircuitBreaker(
            "broker", failure_threshold=1, reset_after_ns=SECOND, success_threshold=2
        )
        breaker.record_failure(0)
        breaker.allows(SECOND)
        breaker.record_success(SECOND)
        assert breaker.state is BreakerState.HALF_OPEN
        breaker.record_success(SECOND)
        assert breaker.state is BreakerState.CLOSED

    def test_an_open_breaker_stops_the_oms_sending(self):
        oms, broker, _ = oms_with()
        oms.breaker = CircuitBreaker("broker", failure_threshold=1)
        oms.breaker.record_failure(0)
        coid = queued(oms)
        outcome = oms.send(coid)
        assert not outcome.sent
        assert "circuit breaker open" in outcome.reason
        assert broker.times_placed(coid) == 0

    def test_breakers_are_per_dependency(self):
        # A market-data outage must not stop the OMS closing a position, and a
        # single global breaker cannot tell the difference.
        a, b = CircuitBreaker("broker"), CircuitBreaker("market-data")
        for _ in range(5):
            b.record_failure(0)
        assert b.is_open
        assert not a.is_open


class TestOutbox:
    def test_every_event_is_queued_for_publication(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        assert len(oms.outbox) == len(oms.log_of(coid))

    def test_draining_publishes_and_clears(self):
        outbox = Outbox()
        event = OrderEvent(EventKind.CREATED, "c", at_ns=0)
        outbox.enqueue(event)
        delivered = outbox.drain(lambda e: None)
        assert delivered == [event]
        assert len(outbox) == 0

    def test_a_failed_publish_is_kept_not_lost(self):
        # The event is already a durable fact. Delivery may be slow; it may not
        # be silent.
        outbox = Outbox()
        outbox.enqueue(OrderEvent(EventKind.CREATED, "c", at_ns=0))

        def boom(event):
            raise RuntimeError("bus down")

        assert outbox.drain(boom) == []
        assert len(outbox) == 1

    def test_repeated_failures_mark_an_entry_exhausted(self):
        outbox = Outbox()
        outbox.enqueue(OrderEvent(EventKind.CREATED, "c", at_ns=0))

        def boom(event):
            raise RuntimeError("bus down")

        for _ in range(3):
            outbox.drain(boom)
        assert outbox.exhausted


class TestDeadLetterQueue:
    def test_an_entry_without_a_reason_is_refused(self):
        # A DLQ whose entries cannot be explained is dropping events with extra
        # steps, and an OMS that drops events loses positions.
        dlq = DeadLetterQueue()
        with pytest.raises(DeterminismError, match="dropped event"):
            dlq.add(OrderEvent(EventKind.FILLED, "c", at_ns=0), "  ", 0)

    def test_entries_are_findable_by_order(self):
        dlq = DeadLetterQueue()
        dlq.add(OrderEvent(EventKind.FILLED, "abc", at_ns=0), "over-fill", 0)
        assert len(dlq.for_order("abc")) == 1
        assert dlq.for_order("other") == []


class TestValidation:
    def test_a_zero_quantity_order_is_refused(self):
        with pytest.raises(DeterminismError, match="must be positive"):
            request(quantity=ZERO)

    def test_a_limit_order_without_a_price_is_refused(self):
        with pytest.raises(DeterminismError, match="needs a price"):
            request(order_type=OrderType.LIMIT)

    def test_a_buy_stop_above_the_entry_is_refused(self):
        # Not a stop -- a guaranteed exit at the worst possible moment.
        with pytest.raises(DeterminismError, match="at or above entry"):
            request(
                order_type=OrderType.LIMIT, limit_price=dec("1.1000"),
                stop_loss=dec("1.1010"),
            )

    def test_a_sell_stop_below_the_entry_is_refused(self):
        with pytest.raises(DeterminismError, match="at or below entry"):
            request(
                side=Side.SELL, order_type=OrderType.LIMIT,
                limit_price=dec("1.1000"), stop_loss=dec("1.0990"),
            )

    def test_a_negative_quantity_event_is_refused(self):
        with pytest.raises(DeterminismError, match="negative quantity"):
            OrderEvent(EventKind.FILLED, "c", at_ns=0, quantity=dec("-1"))


class TestObservability:
    def test_health_reports_what_matters(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        health = oms.health()
        assert "orders" in health
        assert "outbox" in health
        assert "dead letters" in health

    def test_a_halted_oms_says_so_in_its_health(self):
        oms, _, _ = oms_with()
        oms.halt("kill switch")
        assert "HALTED" in oms.health()

    def test_the_history_reads_as_a_narrative(self):
        oms, _, _ = oms_with()
        coid = queued(oms)
        oms.send(coid)
        oms.on_fill(coid, "F-1", QTY, dec("1.1000"))
        history = oms.order(coid).history()
        for kind in ("CREATED", "RISK_APPROVED", "QUEUED", "SENT", "FILLED"):
            assert kind in history
