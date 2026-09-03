"""Durable log tests.

The OMS is event-sourced so a process that dies mid-send can come back knowing
what it did. That promise is only as good as the store, so what is tested here
is mostly what happens when writing goes wrong:

*   A crash during a write leaves a torn last line. That event never completed,
    so dropping it is correct.
*   A torn line *anywhere else* means the middle of the log is gone, and
    rebuilding a position from a hole is worse than refusing to.
*   Nothing is ever rewritten. A store that can rewrite history is a document,
    not an audit log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elyon.modules.execution.domain import (
    CorruptLog,
    EventKind,
    InMemoryEventStore,
    JsonlEventStore,
    ManualClock,
    Oms,
    OrderEvent,
    OrderRequest,
    OrderState,
    OrderType,
    PaperBroker,
    Side,
    TimeInForce,
    client_order_id,
    timeout,
)
from elyon.modules.execution.domain.store import decode_event, encode_event
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

QTY = dec("0.10")


def request(tag: str = "d-1") -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id(tag), correlation_id=tag,
        symbol="EURUSD", side=Side.BUY, quantity=QTY,
    )


def live_oms(store, broker=None, clock=None):
    clock = clock or ManualClock()
    broker = broker or PaperBroker(clock)
    return Oms(broker, clock, store=store), broker, clock


def place_and_fill(oms, tag: str = "d-1", fill: str = "0.04") -> str:
    req = request(tag)
    coid = req.client_order_id
    oms.create(req)
    oms.validate(coid)
    oms.approve_risk(coid)
    oms.queue(coid)
    oms.send(coid)
    if fill:
        oms.on_fill(coid, f"F-{tag}", dec(fill), dec("1.1000"))
    return coid


class TestSurvivingACrash:
    """The property the whole design is for."""

    def test_an_oms_comes_back_as_what_died(self, tmp_path: Path):
        store = JsonlEventStore(tmp_path / "orders.jsonl")
        oms, broker, clock = live_oms(store)
        coid = place_and_fill(oms)
        before = oms.order(coid)

        # The process ends here.
        revived = Oms.restore(JsonlEventStore(tmp_path / "orders.jsonl"),
                              broker, clock)
        after = revived.order(coid)

        assert after.state is before.state
        assert after.filled_quantity == before.filled_quantity
        assert [e.kind for e in after.events] == [e.kind for e in before.events]

    def test_recovery_finds_what_the_broker_did_while_it_was_gone(
        self, tmp_path: Path
    ):
        path = tmp_path / "orders.jsonl"
        oms, broker, clock = live_oms(JsonlEventStore(path))
        coid = place_and_fill(oms, fill="0.04")

        # The venue fills the rest with nobody listening.
        broker.fill(coid, dec("0.06"), dec("1.1002"))

        revived = Oms.restore(JsonlEventStore(path), broker, clock)
        assert revived.order(coid).filled_quantity == dec("0.04")

        revived.recover()
        assert revived.order(coid).filled_quantity == QTY
        assert revived.order(coid).state is OrderState.FILLED

    def test_the_restored_sequence_continues_rather_than_restarting(
        self, tmp_path: Path
    ):
        # Restarting the counter would let a new event claim a slot an old one
        # already holds, and the log would stop being an ordering.
        path = tmp_path / "orders.jsonl"
        oms, broker, clock = live_oms(JsonlEventStore(path))
        # Unfilled, so it is still cancellable: an order carrying a position
        # cannot simply be cancelled, and the machine refuses that.
        coid = place_and_fill(oms, fill="")
        highest = max(e.sequence for e in oms.log_of(coid))

        revived = Oms.restore(JsonlEventStore(path), broker, clock)
        revived.cancel(coid, "after restart")
        assert revived.log_of(coid)[-1].sequence > highest

    def test_restoring_an_empty_store_is_not_an_error(self, tmp_path: Path):
        revived = Oms.restore(
            JsonlEventStore(tmp_path / "nothing.jsonl"),
            PaperBroker(ManualClock()), ManualClock(),
        )
        assert revived.restored_orders == 0

    def test_multiple_orders_all_come_back(self, tmp_path: Path):
        path = tmp_path / "orders.jsonl"
        oms, broker, clock = live_oms(JsonlEventStore(path))
        for tag in ("d-1", "d-2", "d-3"):
            place_and_fill(oms, tag)

        revived = Oms.restore(JsonlEventStore(path), broker, clock)
        assert revived.restored_orders == 3
        assert len(revived.orders) == 3


class TestTornWrites:
    def _log_with(self, tmp_path: Path, tail: str) -> Path:
        path = tmp_path / "orders.jsonl"
        oms, _, _ = live_oms(JsonlEventStore(path))
        place_and_fill(oms)
        with path.open("a") as handle:
            handle.write(tail)
        return path

    def test_a_torn_last_line_is_dropped(self, tmp_path: Path):
        # A crash during a write. The event never completed, so it never
        # happened, and dropping it is the correct reading of the file.
        path = self._log_with(tmp_path, '{"type":"event","kind":"CANC')
        loaded = JsonlEventStore(path).load()
        assert loaded.dropped_torn_tail
        assert loaded.order_count == 1

    def test_a_dropped_tail_is_reported_not_hidden(self, tmp_path: Path):
        path = self._log_with(tmp_path, '{"broken')
        assert JsonlEventStore(path).load().dropped_torn_tail is True

    def test_a_torn_line_in_the_middle_is_refused(self, tmp_path: Path):
        # Not an interrupted write -- damage. Rebuilding a position from a hole
        # is worse than refusing to open the file.
        path = tmp_path / "orders.jsonl"
        oms, _, _ = live_oms(JsonlEventStore(path))
        place_and_fill(oms)
        lines = path.read_text().splitlines()
        lines.insert(3, '{"type":"event","kind":"BR')
        path.write_text("\n".join(lines) + "\n")

        with pytest.raises(CorruptLog, match="damage rather than"):
            JsonlEventStore(path).load()

    def test_the_refusal_names_the_line(self, tmp_path: Path):
        path = tmp_path / "orders.jsonl"
        oms, _, _ = live_oms(JsonlEventStore(path))
        place_and_fill(oms)
        lines = path.read_text().splitlines()
        lines.insert(2, "not json at all")
        path.write_text("\n".join(lines) + "\n")

        with pytest.raises(CorruptLog, match="line 3"):
            JsonlEventStore(path).load()

    def test_an_unknown_record_type_is_refused(self, tmp_path: Path):
        path = tmp_path / "orders.jsonl"
        path.write_text('{"type":"something_else"}\n')
        with pytest.raises(CorruptLog, match="unknown record type"):
            JsonlEventStore(path).load()

    def test_blank_lines_are_ignored(self, tmp_path: Path):
        path = tmp_path / "orders.jsonl"
        oms, _, _ = live_oms(JsonlEventStore(path))
        place_and_fill(oms)
        path.write_text(path.read_text().replace("\n", "\n\n"))
        assert JsonlEventStore(path).load().order_count == 1


class TestLogIntegrity:
    def test_events_out_of_order_are_refused(self):
        # Replaying them would produce a state that never existed.
        store = InMemoryEventStore()
        store.append_order(request())
        coid = request().client_order_id
        for sequence in (1, 3, 2):
            store.append_event(
                OrderEvent(EventKind.CREATED, coid, at_ns=0, sequence=sequence)
            )
        with pytest.raises(CorruptLog, match="out of order"):
            store.load()

    def test_duplicate_sequences_are_refused(self):
        store = InMemoryEventStore()
        coid = request().client_order_id
        store.append_order(request())
        for sequence in (1, 1):
            store.append_event(
                OrderEvent(EventKind.CREATED, coid, at_ns=0, sequence=sequence)
            )
        with pytest.raises(CorruptLog, match="duplicate event sequences"):
            store.load()

    def test_an_order_with_no_events_is_refused(self):
        store = InMemoryEventStore()
        store.append_order(request())
        with pytest.raises(CorruptLog, match="missing at least its creation"):
            store.load().rebuild()

    def test_a_log_that_does_not_fold_is_refused(self):
        # Discovering this on the first order that needs acting on would be
        # much worse than discovering it at load.
        store = InMemoryEventStore()
        req = request()
        store.append_order(req)
        store.append_event(
            OrderEvent(EventKind.SENT, req.client_order_id, at_ns=0, sequence=1)
        )
        with pytest.raises(CorruptLog, match="cannot be rebuilt"):
            store.load().rebuild()


class TestEncoding:
    def test_decimals_survive_exactly(self):
        # Through a float, 1.10005 comes back 1.1000499999999999 and two runs
        # over the same log stop agreeing.
        event = OrderEvent(
            EventKind.FILLED, "coid", at_ns=1, sequence=1,
            broker_event_id="F-1",
            quantity=dec("0.07"), price=dec("1.100050000001"),
        )
        assert decode_event(__import__("json").loads(encode_event(event))) == event

    def test_an_order_survives_the_round_trip(self, tmp_path: Path):
        path = tmp_path / "orders.jsonl"
        original = OrderRequest(
            client_order_id="c1", correlation_id="d1", symbol="XAUUSD",
            side=Side.SELL, quantity=dec("0.03"),
            order_type=OrderType.LIMIT, limit_price=dec("2401.55"),
            stop_loss=dec("2410.00"), take_profit=dec("2380.25"),
            time_in_force=TimeInForce.IOC,
        )
        store = JsonlEventStore(path)
        store.append_order(original)
        assert store.load().requests["c1"] == original

    def test_optional_prices_stay_absent(self, tmp_path: Path):
        store = JsonlEventStore(tmp_path / "orders.jsonl")
        store.append_order(request())
        loaded = store.load().requests[request().client_order_id]
        assert loaded.limit_price is None
        assert loaded.stop_loss is None


class TestAppendOnly:
    def test_writing_never_shortens_the_file(self, tmp_path: Path):
        # A store that can rewrite history is a document, not an audit log.
        path = tmp_path / "orders.jsonl"
        store = JsonlEventStore(path)
        oms, _, _ = live_oms(store)

        sizes = []
        coid = place_and_fill(oms, fill="")
        sizes.append(store.size_bytes)
        oms.on_fill(coid, "F-a", dec("0.05"), dec("1.1000"))
        sizes.append(store.size_bytes)
        oms.on_fill(coid, "F-b", dec("0.05"), dec("1.1001"))
        sizes.append(store.size_bytes)

        assert sizes == sorted(sizes)
        assert len(set(sizes)) == len(sizes)

    def test_every_recorded_event_reaches_the_store(self, tmp_path: Path):
        path = tmp_path / "orders.jsonl"
        oms, _, _ = live_oms(JsonlEventStore(path))
        coid = place_and_fill(oms)
        stored = JsonlEventStore(path).load()
        assert len(stored.events[coid]) == len(oms.log_of(coid))

    def test_a_failed_send_is_still_recorded(self, tmp_path: Path):
        # The record of an attempt is exactly what recovery needs; losing it
        # would leave a restarted process unaware anything was tried.
        path = tmp_path / "orders.jsonl"
        clock = ManualClock()
        broker = PaperBroker(clock, accept_despite_failure=True)
        broker.fail_place = [timeout()]
        oms = Oms(broker, clock, store=JsonlEventStore(path))
        coid = place_and_fill(oms, fill="")

        kinds = [e.kind for e in JsonlEventStore(path).load().events[coid]]
        assert EventKind.SENT in kinds
        assert EventKind.RECOVERY_STARTED in kinds


class TestDurabilityIsDeliberate:
    def test_fsync_is_on_by_default(self, tmp_path: Path):
        # "Persisted" that survives a process crash but not a power cut is not
        # the guarantee the OMS is relying on.
        assert JsonlEventStore(tmp_path / "a.jsonl").fsync is True

    def test_it_can_be_turned_off_explicitly(self, tmp_path: Path):
        store = JsonlEventStore(tmp_path / "b.jsonl", fsync=False)
        store.append_order(request())
        assert store.load().order_count == 1

    def test_the_directory_is_created(self, tmp_path: Path):
        store = JsonlEventStore(tmp_path / "nested" / "deep" / "orders.jsonl")
        store.append_order(request())
        assert store.path.exists()
