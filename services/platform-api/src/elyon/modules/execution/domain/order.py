"""The order aggregate.

State is never assigned; it is *folded* from the event log. That is not a
stylistic preference -- it is what lets a process that died halfway through a
send come back, replay, and know precisely what it had already done.

The transition table is **data**. Writing the state machine as branching code
means every new state is another place to forget a guard; written as a table it
can be inspected, tested exhaustively, and proved to have no path from a
terminal state back into an active one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from typing import Final, Mapping, Sequence

from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec, quantize

from .events import EventKind, OrderEvent, OrderType, Side, TimeInForce


class OrderState(str, Enum):
    """Where an order stands. ENG-006 §3.1-3.2."""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    RISK_APPROVED = "RISK_APPROVED"
    QUEUED = "QUEUED"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    MANAGED = "MANAGED"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"
    # Exceptional
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    SAFE_HALT = "SAFE_HALT"
    RECOVERY = "RECOVERY"
    ARCHIVED = "ARCHIVED"

    @property
    def is_terminal(self) -> bool:
        """Once here, the outcome is sealed and cannot be reopened."""
        return self in TERMINAL_STATES

    @property
    def is_live_at_broker(self) -> bool:
        """Whether the broker may still act on this order.

        The set that matters for reconciliation: these are the states where a
        fill can still arrive out of nowhere.
        """
        return self in {
            OrderState.SENT, OrderState.ACKNOWLEDGED,
            OrderState.PARTIALLY_FILLED, OrderState.RECOVERY,
        }

    @property
    def has_exposure(self) -> bool:
        """Whether real money is currently at risk."""
        return self in {
            OrderState.PARTIALLY_FILLED, OrderState.FILLED,
            OrderState.MANAGED, OrderState.PARTIALLY_CLOSED,
        }


TERMINAL_STATES: Final[frozenset[OrderState]] = frozenset({
    OrderState.CLOSED, OrderState.REJECTED, OrderState.CANCELLED,
    OrderState.EXPIRED, OrderState.ARCHIVED,
})


# The machine, as data. Each event kind maps the states it may be applied from
# to the state it produces. An event arriving in any other state is a bug, and
# the aggregate says so rather than quietly moving anyway.
TRANSITIONS: Final[Mapping[EventKind, Mapping[OrderState, OrderState]]] = {
    EventKind.CREATED: {},  # creation is the entry point, handled separately
    EventKind.VALIDATED: {OrderState.CREATED: OrderState.VALIDATED},
    EventKind.RISK_APPROVED: {OrderState.VALIDATED: OrderState.RISK_APPROVED},
    EventKind.QUEUED: {OrderState.RISK_APPROVED: OrderState.QUEUED},
    # Exactly one QUEUED -> SENT per client_order_id. The absence of any other
    # source state is what makes duplicate sends impossible (§0.1 rule 6).
    EventKind.SENT: {OrderState.QUEUED: OrderState.SENT},
    EventKind.ACKNOWLEDGED: {
        OrderState.SENT: OrderState.ACKNOWLEDGED,
        OrderState.RECOVERY: OrderState.ACKNOWLEDGED,
    },
    EventKind.PARTIALLY_FILLED: {
        OrderState.SENT: OrderState.PARTIALLY_FILLED,
        OrderState.ACKNOWLEDGED: OrderState.PARTIALLY_FILLED,
        OrderState.PARTIALLY_FILLED: OrderState.PARTIALLY_FILLED,
        OrderState.RECOVERY: OrderState.PARTIALLY_FILLED,
    },
    EventKind.FILLED: {
        OrderState.SENT: OrderState.FILLED,
        OrderState.ACKNOWLEDGED: OrderState.FILLED,
        OrderState.PARTIALLY_FILLED: OrderState.FILLED,
        OrderState.RECOVERY: OrderState.FILLED,
    },
    EventKind.REJECTED: {
        OrderState.CREATED: OrderState.REJECTED,
        OrderState.VALIDATED: OrderState.REJECTED,
        OrderState.RISK_APPROVED: OrderState.REJECTED,
        OrderState.QUEUED: OrderState.REJECTED,
        OrderState.SENT: OrderState.REJECTED,
        OrderState.RECOVERY: OrderState.REJECTED,
    },
    EventKind.CANCELLED: {
        OrderState.QUEUED: OrderState.CANCELLED,
        OrderState.SENT: OrderState.CANCELLED,
        OrderState.ACKNOWLEDGED: OrderState.CANCELLED,
        OrderState.RECOVERY: OrderState.CANCELLED,
    },
    EventKind.EXPIRED: {
        OrderState.QUEUED: OrderState.EXPIRED,
        OrderState.SENT: OrderState.EXPIRED,
        OrderState.ACKNOWLEDGED: OrderState.EXPIRED,
    },
    EventKind.FAILED: {
        OrderState.QUEUED: OrderState.FAILED,
        OrderState.SENT: OrderState.FAILED,
        OrderState.RECOVERY: OrderState.FAILED,
    },
    # Recovery is reachable from anything the broker might still be acting on.
    EventKind.RECOVERY_STARTED: {
        OrderState.SENT: OrderState.RECOVERY,
        OrderState.ACKNOWLEDGED: OrderState.RECOVERY,
        OrderState.PARTIALLY_FILLED: OrderState.RECOVERY,
        OrderState.FAILED: OrderState.RECOVERY,
        OrderState.SAFE_HALT: OrderState.RECOVERY,
    },
    EventKind.RECONCILED: {},   # lands wherever the broker says; see _apply
    EventKind.SAFE_HALTED: {
        OrderState.QUEUED: OrderState.SAFE_HALT,
        OrderState.RISK_APPROVED: OrderState.SAFE_HALT,
        OrderState.VALIDATED: OrderState.SAFE_HALT,
        OrderState.CREATED: OrderState.SAFE_HALT,
    },
    EventKind.RESUMED: {OrderState.SAFE_HALT: OrderState.QUEUED},
    EventKind.CLOSED: {
        OrderState.FILLED: OrderState.CLOSED,
        OrderState.MANAGED: OrderState.CLOSED,
        OrderState.PARTIALLY_CLOSED: OrderState.CLOSED,
    },
    EventKind.ARCHIVED: {
        OrderState.CLOSED: OrderState.ARCHIVED,
        OrderState.REJECTED: OrderState.ARCHIVED,
        OrderState.CANCELLED: OrderState.ARCHIVED,
        OrderState.EXPIRED: OrderState.ARCHIVED,
        OrderState.FAILED: OrderState.ARCHIVED,
    },
}


class IllegalTransition(DeterminismError):
    """An event arrived in a state that cannot accept it."""


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """What the strategy asked for, before anything happened."""

    client_order_id: str
    correlation_id: str
    symbol: str
    side: Side
    quantity: Decimal
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC

    def __post_init__(self) -> None:
        if self.quantity <= ZERO:
            raise DeterminismError(f"order quantity must be positive, got {self.quantity}")
        if self.order_type is not OrderType.MARKET and self.limit_price is None:
            raise DeterminismError(
                f"{self.order_type.value} order needs a price"
            )
        # A protective stop on the wrong side is not a stop, it is a guaranteed
        # exit at the worst possible moment.
        if self.stop_loss is not None and self.limit_price is not None:
            if self.side is Side.BUY and self.stop_loss >= self.limit_price:
                raise DeterminismError(
                    f"buy stop {self.stop_loss} at or above entry {self.limit_price}"
                )
            if self.side is Side.SELL and self.stop_loss <= self.limit_price:
                raise DeterminismError(
                    f"sell stop {self.stop_loss} at or below entry {self.limit_price}"
                )


@dataclass(frozen=True, slots=True)
class Fill:
    """One execution. Orders fill in pieces more often than in one go."""

    broker_event_id: str
    quantity: Decimal
    price: Decimal
    at_ns: int

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price


@dataclass(frozen=True, slots=True)
class Order:
    """The aggregate: a projection of its own event log.

    Immutable. Applying an event returns a new order, so the state that produced
    a decision stays pinned to that decision instead of being a mutable thing
    that has already moved on by the time anyone looks.
    """

    request: OrderRequest
    state: OrderState
    events: tuple[OrderEvent, ...] = ()
    fills: tuple[Fill, ...] = ()
    broker_order_id: str | None = None
    send_attempts: int = 0
    # Broker event ids already folded in, so a redelivered report is a no-op.
    seen_broker_events: frozenset[str] = frozenset()

    # -- identity ---------------------------------------------------------

    @property
    def client_order_id(self) -> str:
        return self.request.client_order_id

    @property
    def symbol(self) -> str:
        return self.request.symbol

    # -- exposure ---------------------------------------------------------

    @property
    def filled_quantity(self) -> Decimal:
        return sum((f.quantity for f in self.fills), ZERO)

    @property
    def remaining_quantity(self) -> Decimal:
        return self.request.quantity - self.filled_quantity

    @property
    def is_fully_filled(self) -> bool:
        return self.filled_quantity >= self.request.quantity

    @property
    def average_fill_price(self) -> Decimal | None:
        """Volume-weighted, because a simple mean of prices is a different number.

        Averaging the prices of a 0.9-lot fill and a 0.1-lot fill as though they
        were equal misstates the entry, and every risk figure derived from it.
        """
        total = self.filled_quantity
        if total == ZERO:
            return None
        notional = sum((f.notional for f in self.fills), ZERO)
        return quantize(notional / total, 8)

    def check_conservation(self) -> None:
        """Fills must never exceed what was ordered (ENG-006 §3.4).

        Divergence means the broker and the OMS disagree about reality, which is
        a reconciliation problem, never something to average away.
        """
        if self.filled_quantity > self.request.quantity:
            raise DeterminismError(
                f"{self.client_order_id}: filled {self.filled_quantity} against "
                f"an order for {self.request.quantity}; the broker and the OMS "
                f"disagree and this needs reconciliation, not arithmetic"
            )

    # -- folding ----------------------------------------------------------

    def apply(self, event: OrderEvent) -> "Order":
        """Fold one event in, or refuse it."""
        if event.client_order_id != self.client_order_id:
            raise DeterminismError(
                f"event for {event.client_order_id} applied to "
                f"{self.client_order_id}"
            )

        # Deduplication. A broker that redelivers a fill must not double it.
        if event.from_broker and event.broker_event_id in self.seen_broker_events:
            return self

        if self.state.is_terminal and event.kind is not EventKind.ARCHIVED:
            raise IllegalTransition(
                f"{self.client_order_id} is {self.state.value} (terminal); "
                f"{event.kind.value} cannot reopen it"
            )

        return self._apply(event)

    def _apply(self, event: OrderEvent) -> "Order":
        updates: dict[str, object] = {}
        state = self.state

        if event.kind is EventKind.RECONCILED:
            # The broker is the authority (ADR-EXE-8). Whatever it says the
            # order is, that is what it is.
            target = OrderState(event.payload.get("state", self.state.value))
            state = target
        else:
            allowed = TRANSITIONS.get(event.kind, {})
            if self.state not in allowed:
                raise IllegalTransition(
                    f"{self.client_order_id}: cannot apply {event.kind.value} "
                    f"in state {self.state.value}"
                )
            state = allowed[self.state]

        if event.kind is EventKind.SENT:
            updates["send_attempts"] = self.send_attempts + 1
        if event.kind is EventKind.ACKNOWLEDGED:
            updates["broker_order_id"] = event.payload.get(
                "brokerOrderId", self.broker_order_id
            )
        if event.kind in (EventKind.PARTIALLY_FILLED, EventKind.FILLED):
            fill = Fill(
                broker_event_id=event.broker_event_id or "",
                quantity=event.quantity,
                price=event.price,
                at_ns=event.at_ns,
            )
            updates["fills"] = self.fills + (fill,)

        seen = self.seen_broker_events
        if event.from_broker and event.broker_event_id:
            seen = seen | {event.broker_event_id}

        folded = replace(
            self,
            state=state,
            events=self.events + (event,),
            seen_broker_events=seen,
            **updates,  # type: ignore[arg-type]
        )
        folded.check_conservation()
        return folded

    # -- construction -----------------------------------------------------

    @classmethod
    def create(cls, request: OrderRequest, at_ns: int) -> "Order":
        event = OrderEvent(
            kind=EventKind.CREATED,
            client_order_id=request.client_order_id,
            at_ns=at_ns,
            payload={"symbol": request.symbol, "side": request.side.value},
        )
        return cls(request=request, state=OrderState.CREATED, events=(event,))

    @classmethod
    def replay(cls, request: OrderRequest, events: Sequence[OrderEvent]) -> "Order":
        """Rebuild an order from its log.

        The guarantee this exists for: the same events always produce the same
        order, so a restarted process reaches the state it had before the crash
        rather than a plausible-looking guess at it.
        """
        if not events:
            raise DeterminismError(f"{request.client_order_id}: empty event log")
        if events[0].kind is not EventKind.CREATED:
            raise DeterminismError(
                f"{request.client_order_id}: log starts with "
                f"{events[0].kind.value}, not CREATED"
            )

        order = cls(request=request, state=OrderState.CREATED, events=(events[0],))
        for event in events[1:]:
            order = order.apply(event)
        return order

    # -- reporting --------------------------------------------------------

    def history(self) -> str:
        return "\n".join(str(e) for e in self.events)

    def __str__(self) -> str:
        filled = (
            f" {self.filled_quantity}/{self.request.quantity}"
            if self.fills else ""
        )
        return (
            f"{self.client_order_id} {self.request.side.value} "
            f"{self.symbol} {self.state.value}{filled}"
        )
