"""MT5 adapter tests.

The MetaTrader5 package is Windows-only and needs a running terminal, so what
is tested here is the part that can be: the mapping. That is also where the
bugs are. A retcode classified wrongly is a wrong answer to the only question
the OMS asks -- is this outcome a fact, or a question? -- and the consequence
of getting it backwards is an untracked position.

The other half is ``query``. On MT5 there is no venue-side deduplication, so
query-before-resend carries the entire weight of preventing duplicate
positions, and an order it fails to find is an order the OMS will place twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from elyon.modules.execution.domain import (
    BrokerError,
    BrokerErrorKind,
    ManualClock,
    OrderRequest,
    OrderState,
    OrderType,
    Side,
    check_adapter,
    client_order_id,
)
from elyon.modules.execution.infrastructure.mt5 import (
    COMMENT_MAX,
    REJECTION_CODES,
    RETCODE_DONE,
    SUCCESS_CODES,
    UNKNOWN_CODES,
    Mt5Adapter,
    Mt5Config,
    order_tag,
)
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

MAGIC = 20260101


# ---------------------------------------------------------------------------
# A stand-in for the terminal
# ---------------------------------------------------------------------------

@dataclass
class Result:
    retcode: int
    order: int = 0
    deal: int = 0
    comment: str = ""


@dataclass
class Record:
    ticket: int
    magic: int
    comment: str
    volume: float = 0.10
    price_open: float = 1.1000
    price: float = 1.1000
    time: int = 1768483800
    order: int = 0


@dataclass
class FakeMt5:
    """Enough of the terminal to exercise the mapping."""

    TRADE_ACTION_DEAL: int = 1
    TRADE_ACTION_PENDING: int = 5
    TRADE_ACTION_REMOVE: int = 2
    ORDER_TYPE_BUY: int = 0
    ORDER_TYPE_SELL: int = 1
    ORDER_TYPE_BUY_LIMIT: int = 2
    ORDER_TYPE_SELL_LIMIT: int = 3
    ORDER_TIME_GTC: int = 0

    next_result: Any = field(default_factory=lambda: Result(RETCODE_DONE, order=555))
    pending: list = field(default_factory=list)
    positions: list = field(default_factory=list)
    deals: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    error: tuple = (0, "ok")
    # None from a lookup means the call failed, not that nothing was found.
    lookups_fail: bool = False

    def order_send(self, payload):
        self.sent.append(payload)
        return self.next_result

    def orders_get(self, *args):
        return None if self.lookups_fail else list(self.pending)

    def positions_get(self, *args):
        return None if self.lookups_fail else list(self.positions)

    def history_deals_get(self, *args):
        return None if self.lookups_fail else list(self.deals)

    def last_error(self):
        return self.error


def adapter(fake: FakeMt5 | None = None, **config) -> tuple[Mt5Adapter, FakeMt5]:
    terminal = fake or FakeMt5()
    settings = {"magic": MAGIC, "settle_seconds": 0}
    settings.update(config)
    return (
        Mt5Adapter(ManualClock(), Mt5Config(**settings), mt5=terminal),
        terminal,
    )


def request(tag: str = "d-1", **kwargs) -> OrderRequest:
    settings = dict(
        client_order_id=client_order_id(tag), correlation_id=tag,
        symbol="EURUSD", side=Side.BUY, quantity=dec("0.10"),
    )
    settings.update(kwargs)
    return OrderRequest(**settings)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

class TestOrderTagging:
    def test_the_tag_fits_in_an_mt5_comment(self):
        # A UUID is 36 characters and the field holds about 31.
        assert len(order_tag(client_order_id("d-1"))) <= COMMENT_MAX

    def test_it_is_deterministic(self):
        coid = client_order_id("d-1")
        assert order_tag(coid) == order_tag(coid)

    def test_different_orders_get_different_tags(self):
        assert order_tag(client_order_id("a")) != order_tag(client_order_id("b"))

    def test_ids_sharing_a_prefix_do_not_collide(self):
        # Truncating instead of hashing would let two orders share a tag, and a
        # collision here means adopting the wrong order.
        assert order_tag("aaaaaaaa-0000") != order_tag("aaaaaaaa-1111")

    def test_the_tag_reaches_the_terminal(self):
        mt5, fake = adapter()
        req = request()
        mt5.place(req, "key")
        assert fake.sent[0]["comment"] == order_tag(req.client_order_id)
        assert fake.sent[0]["magic"] == MAGIC


# ---------------------------------------------------------------------------
# Retcode classification
# ---------------------------------------------------------------------------

class TestClassifyingOutcomes:
    """The only question the OMS asks: fact, or question?"""

    def _place_with(self, retcode: int, comment: str = ""):
        mt5, fake = adapter()
        fake.next_result = Result(retcode, comment=comment)
        with pytest.raises(BrokerError) as caught:
            mt5.place(request(), "key")
        return caught.value

    def test_success_returns_an_ack(self):
        mt5, fake = adapter()
        fake.next_result = Result(RETCODE_DONE, order=777)
        assert mt5.place(request(), "key").broker_order_id == "777"

    @pytest.mark.parametrize("retcode", sorted(SUCCESS_CODES))
    def test_every_success_code_is_accepted(self, retcode):
        mt5, fake = adapter()
        fake.next_result = Result(retcode, order=1)
        mt5.place(request(), "key")   # must not raise

    def test_insufficient_funds_is_a_fact(self):
        error = self._place_with(10019)
        assert not error.outcome_is_unknown
        assert "insufficient funds" in str(error)

    def test_market_closed_is_a_fact(self):
        assert not self._place_with(10018).outcome_is_unknown

    def test_invalid_stops_cannot_be_retried(self):
        assert self._place_with(10016).kind is BrokerErrorKind.INVALID

    def test_too_many_requests_is_throttling(self):
        assert self._place_with(10024).kind is BrokerErrorKind.THROTTLED

    def test_no_connection_is_unavailable(self):
        error = self._place_with(10031)
        assert error.kind is BrokerErrorKind.UNAVAILABLE
        assert error.outcome_is_unknown

    def test_an_unrecognised_code_is_treated_as_unknown(self):
        # The dangerous direction is the other one: calling an applied order
        # "rejected" leaves a position nobody is tracking. Calling a rejection
        # "unknown" costs one wasted query.
        error = self._place_with(99999, "who knows")
        assert error.outcome_is_unknown
        assert "unrecognised retcode" in str(error)

    @pytest.mark.parametrize("retcode", sorted(UNKNOWN_CODES))
    def test_explicit_unknowns_reconcile(self, retcode):
        assert self._place_with(retcode).outcome_is_unknown

    def test_no_result_at_all_is_unknown(self):
        mt5, fake = adapter()
        fake.next_result = None
        fake.error = (-10005, "timeout")
        with pytest.raises(BrokerError) as caught:
            mt5.place(request(), "key")
        assert caught.value.outcome_is_unknown
        assert "may or may not exist" in str(caught.value)

    def test_every_rejection_code_maps_to_a_known_outcome(self):
        # The whole table, so a future edit cannot quietly move one into the
        # unknown bucket and change when the OMS reconciles.
        for retcode in REJECTION_CODES:
            if retcode == 10031:      # no connection: genuinely unknown
                continue
            assert not self._place_with(retcode).outcome_is_unknown, retcode


# ---------------------------------------------------------------------------
# query -- the whole safety mechanism on this venue
# ---------------------------------------------------------------------------

class TestFindingAnOrder:
    def _tagged(self, coid: str, **kwargs) -> Record:
        return Record(ticket=1, magic=MAGIC, comment=order_tag(coid), **kwargs)

    def test_a_pending_order_is_found(self):
        req = request()
        mt5, fake = adapter()
        fake.pending = [self._tagged(req.client_order_id)]
        state = mt5.query(req.client_order_id)
        assert state.exists
        assert state.state is OrderState.ACKNOWLEDGED

    def test_an_open_position_is_found(self):
        req = request()
        mt5, fake = adapter()
        fake.positions = [self._tagged(req.client_order_id)]
        state = mt5.query(req.client_order_id)
        assert state.exists
        assert state.filled_quantity == dec("0.10")

    def test_a_closed_position_is_found_in_history(self):
        # The case that matters after a restart: it filled and closed while
        # nobody was watching. Not searching here reports exists=False for an
        # order that did happen, and on MT5 that means placing a second one.
        req = request()
        mt5, fake = adapter()
        fake.deals = [self._tagged(req.client_order_id, order=99)]
        state = mt5.query(req.client_order_id)
        assert state.exists
        assert state.broker_order_id == "99"

    def test_an_order_never_placed_is_absent(self):
        mt5, _ = adapter()
        assert mt5.query(client_order_id("never")).exists is False

    def test_another_systems_order_is_not_claimed(self):
        # Same account, different magic. Claiming it would have the OMS adopt a
        # position it did not open and manage a stranger's trade.
        req = request()
        mt5, fake = adapter()
        fake.positions = [
            Record(ticket=1, magic=999, comment=order_tag(req.client_order_id))
        ]
        assert mt5.query(req.client_order_id).exists is False

    def test_our_magic_but_a_different_order_is_not_claimed(self):
        mt5, fake = adapter()
        fake.positions = [
            Record(ticket=1, magic=MAGIC, comment=order_tag("someone else"))
        ]
        assert mt5.query(client_order_id("d-1")).exists is False

    def test_a_failed_lookup_is_not_read_as_absent(self):
        # The duplicate-position bug in disguise: a lookup that could not run
        # is not an empty result, and query must never answer "no such order"
        # because it could not look.
        mt5, fake = adapter()
        fake.lookups_fail = True
        fake.error = (-10004, "no connection")
        with pytest.raises(BrokerError) as caught:
            mt5.query(client_order_id("d-1"))
        assert caught.value.outcome_is_unknown
        assert "must not be read as" in str(caught.value)


# ---------------------------------------------------------------------------
# Request shaping
# ---------------------------------------------------------------------------

class TestBuildingTheRequest:
    def test_the_exness_suffix_is_applied(self):
        # Standard and Cent accounts append it; the strategy layer should not
        # have to know which account was opened.
        mt5, fake = adapter(symbol_suffix="m")
        mt5.place(request(), "key")
        assert fake.sent[0]["symbol"] == "EURUSDm"

    def test_no_suffix_by_default(self):
        mt5, fake = adapter()
        mt5.place(request(), "key")
        assert fake.sent[0]["symbol"] == "EURUSD"

    def test_a_market_buy_is_a_deal(self):
        mt5, fake = adapter()
        mt5.place(request(), "key")
        assert fake.sent[0]["action"] == fake.TRADE_ACTION_DEAL
        assert fake.sent[0]["type"] == fake.ORDER_TYPE_BUY

    def test_a_sell_is_a_sell(self):
        mt5, fake = adapter()
        mt5.place(request(side=Side.SELL), "key")
        assert fake.sent[0]["type"] == fake.ORDER_TYPE_SELL

    def test_a_limit_order_becomes_pending(self):
        mt5, fake = adapter()
        mt5.place(
            request(order_type=OrderType.LIMIT, limit_price=dec("1.0950")), "key"
        )
        assert fake.sent[0]["action"] == fake.TRADE_ACTION_PENDING
        assert fake.sent[0]["price"] == 1.0950

    def test_protective_levels_travel_with_the_order(self):
        # Sent with the order rather than added afterwards: a fill that lands
        # between placing and attaching a stop is an unprotected position.
        mt5, fake = adapter()
        mt5.place(
            request(
                order_type=OrderType.LIMIT, limit_price=dec("1.1000"),
                stop_loss=dec("1.0950"), take_profit=dec("1.1100"),
            ),
            "key",
        )
        assert fake.sent[0]["sl"] == 1.0950
        assert fake.sent[0]["tp"] == 1.1100

    def test_absent_levels_are_omitted_not_zeroed(self):
        # A zero stop on MT5 means "no stop", but sending the key at all
        # invites a future edit to send 0.0 as a price.
        mt5, fake = adapter()
        mt5.place(request(), "key")
        assert "sl" not in fake.sent[0]
        assert "tp" not in fake.sent[0]


class TestConfiguration:
    def test_an_out_of_range_magic_is_refused(self):
        with pytest.raises(DeterminismError, match="magic must be"):
            Mt5Config(magic=0)

    def test_a_negative_settle_time_is_refused(self):
        with pytest.raises(DeterminismError, match="negative"):
            Mt5Config(settle_seconds=-1)

    def test_the_missing_package_explains_itself(self):
        from elyon.modules.execution.infrastructure.mt5 import _import_mt5
        with pytest.raises(DeterminismError, match="Windows-only"):
            _import_mt5()


class TestAgainstTheConformanceSuite:
    def test_the_adapter_passes_every_critical_check(self):
        # Against a well-behaved fake terminal. The real one has to be checked
        # with `elyon conformance` on a demo account -- especially the
        # deduplication check, which MT5 is expected to fail.
        def factory(clock):
            fake = FakeMt5()
            built = Mt5Adapter(clock, Mt5Config(magic=MAGIC, settle_seconds=0),
                               mt5=fake)
            original_send = fake.order_send

            def send(payload):
                # A conforming terminal would at least register the order so a
                # later query can find it.
                result = original_send(payload)
                fake.positions.append(Record(
                    ticket=len(fake.positions) + 1, magic=MAGIC,
                    comment=payload.get("comment", ""),
                ))
                return result

            fake.order_send = send
            return built

        report = check_adapter(factory, ManualClock())
        critical = [c for c in report.checks if c.critical]
        assert all(c.passed for c in critical), str(report)

    def test_mt5_cannot_deduplicate_and_the_suite_says_so(self):
        # The finding that matters most on this venue: MT5 has no client order
        # id, so the same request placed twice produces two positions. The OMS
        # never sends twice by itself, but the safety margin is gone.
        tickets = {"n": 0}

        def factory(clock):
            fake = FakeMt5()
            built = Mt5Adapter(clock, Mt5Config(magic=MAGIC, settle_seconds=0),
                               mt5=fake)

            def send(payload):
                tickets["n"] += 1
                fake.positions.append(Record(
                    ticket=tickets["n"], magic=MAGIC,
                    comment=payload.get("comment", ""),
                ))
                return Result(RETCODE_DONE, order=tickets["n"])

            fake.order_send = send
            return built

        report = check_adapter(factory, ManualClock())
        dedupe = next(
            c for c in report.checks if c.name == "duplicate place is deduplicated"
        )
        assert not dedupe.passed
        assert "double the position" in dedupe.detail
