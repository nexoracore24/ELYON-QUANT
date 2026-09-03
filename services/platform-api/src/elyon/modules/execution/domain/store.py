"""Durable storage for the order log.

The OMS is event-sourced so that a process which dies mid-send can come back and
know exactly what it had done. That promise is empty without somewhere to come
back *from*, and this is that place.

The format is append-only JSON lines. Not because it is clever, but because of
what it costs when things go wrong at three in the morning: it is greppable,
tailable, diffable, needs no server, and a corrupt file can be repaired by hand.
A database would be faster and much harder to reason about at exactly the moment
reasoning matters most.

Three properties this file exists to hold:

*   **Append-only.** Nothing is ever rewritten. A store that can rewrite history
    is not an audit log, it is a document.
*   **Durable on return.** ``append`` fsyncs by default, because "persisted"
    that survives a process crash but not a power cut is not the guarantee the
    OMS is relying on.
*   **Honest about damage.** A crash during a write leaves a torn last line.
    That one is dropped -- the event never completed. A torn line anywhere
    *else* is corruption, and loading fails loudly rather than silently
    reconstructing a position from a hole.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Protocol

from elyon.shared_kernel.edcs.canonical import canonical_decimal
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec

from .events import EventKind, OrderEvent, OrderType, Side, TimeInForce
from .order import Order, OrderRequest

RECORD_ORDER = "order"
RECORD_EVENT = "event"


class CorruptLog(DeterminismError):
    """The log cannot be trusted to describe what happened."""


class EventStore(Protocol):
    """Where the log lives."""

    def append_order(self, request: OrderRequest) -> None: ...
    def append_event(self, event: OrderEvent) -> None: ...
    def load(self) -> "LoadedLog": ...


@dataclass(frozen=True, slots=True)
class LoadedLog:
    """Everything a restarted OMS needs to rebuild itself."""

    requests: dict[str, OrderRequest]
    events: dict[str, list[OrderEvent]]
    last_sequence: int
    dropped_torn_tail: bool = False

    @property
    def order_count(self) -> int:
        return len(self.requests)

    @property
    def event_count(self) -> int:
        return sum(len(e) for e in self.events.values())

    def rebuild(self) -> dict[str, Order]:
        """Fold every log back into an order.

        A failure here means the stored events do not describe a reachable
        state, which is a data problem rather than a code one -- so it is
        reported with the order that broke rather than swallowed.
        """
        orders: dict[str, Order] = {}
        for coid, request in self.requests.items():
            log = self.events.get(coid)
            if not log:
                raise CorruptLog(
                    f"{coid} was recorded but has no events; the log is "
                    f"missing at least its creation"
                )
            try:
                orders[coid] = Order.replay(request, log)
            except DeterminismError as exc:
                raise CorruptLog(
                    f"{coid} cannot be rebuilt from its log: {exc}"
                ) from exc
        return orders


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def encode_order(request: OrderRequest) -> str:
    payload = {
        "type": RECORD_ORDER,
        "clientOrderId": request.client_order_id,
        "correlationId": request.correlation_id,
        "symbol": request.symbol,
        "side": request.side.value,
        "quantity": canonical_decimal(request.quantity),
        "orderType": request.order_type.value,
        "timeInForce": request.time_in_force.value,
    }
    for name, value in (
        ("limitPrice", request.limit_price),
        ("stopLoss", request.stop_loss),
        ("takeProfit", request.take_profit),
    ):
        if value is not None:
            payload[name] = canonical_decimal(value)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def encode_event(event: OrderEvent) -> str:
    payload = {"type": RECORD_EVENT, **event.to_canonical_dict()}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def decode_order(raw: dict) -> OrderRequest:
    return OrderRequest(
        client_order_id=raw["clientOrderId"],
        correlation_id=raw["correlationId"],
        symbol=raw["symbol"],
        side=Side(raw["side"]),
        quantity=dec(raw["quantity"]),
        order_type=OrderType(raw.get("orderType", "MARKET")),
        limit_price=_maybe(raw.get("limitPrice")),
        stop_loss=_maybe(raw.get("stopLoss")),
        take_profit=_maybe(raw.get("takeProfit")),
        time_in_force=TimeInForce(raw.get("timeInForce", "GTC")),
    )


def decode_event(raw: dict) -> OrderEvent:
    return OrderEvent(
        kind=EventKind(raw["kind"]),
        client_order_id=raw["clientOrderId"],
        at_ns=int(raw["at"]),
        sequence=int(raw["sequence"]),
        broker_event_id=raw.get("brokerEventId"),
        quantity=dec(raw.get("quantity", "0")),
        price=dec(raw.get("price", "0")),
        reason=raw.get("reason", ""),
        payload=dict(raw.get("payload", {})),
    )


def _maybe(value: str | None) -> Decimal | None:
    return None if value is None else dec(value)


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class InMemoryEventStore:
    """For tests and for backtests, where durability is not the point."""

    lines: list[str] = field(default_factory=list)

    def append_order(self, request: OrderRequest) -> None:
        self.lines.append(encode_order(request))

    def append_event(self, event: OrderEvent) -> None:
        self.lines.append(encode_event(event))

    def load(self) -> LoadedLog:
        return _parse(self.lines, source="memory", tolerate_torn_tail=False)


@dataclass(slots=True)
class JsonlEventStore:
    """Append-only JSON lines on disk.

    ``fsync`` defaults to True. Turning it off makes writes far faster and
    turns "persisted" into "persisted unless the machine loses power", which is
    a reasonable trade for a backtest and not one for a live account -- so it
    has to be chosen deliberately.
    """

    path: Path
    fsync: bool = True

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            if self.fsync:
                os.fsync(handle.fileno())

    def append_order(self, request: OrderRequest) -> None:
        self._append(encode_order(request))

    def append_event(self, event: OrderEvent) -> None:
        self._append(encode_event(event))

    def load(self) -> LoadedLog:
        if not self.path.exists():
            return LoadedLog(requests={}, events={}, last_sequence=0)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return _parse(lines, source=str(self.path), tolerate_torn_tail=True)

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0


def _parse(
    lines: list[str], *, source: str, tolerate_torn_tail: bool
) -> LoadedLog:
    requests: dict[str, OrderRequest] = {}
    events: dict[str, list[OrderEvent]] = {}
    last_sequence = 0
    dropped_tail = False

    total = len(lines)
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError as exc:
            # A crash during a write leaves the final line half-written. That
            # event never completed, so dropping it is correct. A broken line
            # anywhere else means the middle of the log is missing, and
            # rebuilding a position from a hole is worse than refusing to.
            if tolerate_torn_tail and number == total:
                dropped_tail = True
                continue
            raise CorruptLog(
                f"{source} line {number}: not valid JSON ({exc.msg}). This is "
                f"not the last line, so it is damage rather than an "
                f"interrupted write."
            ) from exc

        kind = raw.get("type")
        if kind == RECORD_ORDER:
            request = decode_order(raw)
            requests[request.client_order_id] = request
        elif kind == RECORD_EVENT:
            event = decode_event(raw)
            events.setdefault(event.client_order_id, []).append(event)
            last_sequence = max(last_sequence, event.sequence)
        else:
            raise CorruptLog(
                f"{source} line {number}: unknown record type {kind!r}"
            )

    _check_sequences(events, source)
    return LoadedLog(
        requests=requests,
        events=events,
        last_sequence=last_sequence,
        dropped_torn_tail=dropped_tail,
    )


def _check_sequences(
    events: dict[str, list[OrderEvent]], source: str
) -> None:
    """Every event must be in order within its own order's log.

    Out-of-order events would fold into a different state than the one that
    actually happened, which is the one thing the log exists to prevent.
    """
    for coid, log in events.items():
        sequences = [e.sequence for e in log]
        if sequences != sorted(sequences):
            raise CorruptLog(
                f"{source}: events for {coid} are out of order ({sequences}); "
                f"replaying them would produce a state that never existed"
            )
        if len(set(sequences)) != len(sequences):
            raise CorruptLog(
                f"{source}: {coid} has duplicate event sequences ({sequences})"
            )
