"""A paper broker.

Its job is not to be realistic. Its job is to be able to fail in the specific
ways real brokers fail -- timing out with the order already placed, redelivering
a fill, going dark mid-reconciliation -- because those are the paths that are
never exercised by a broker that always works, and they are the ones that lose
money when they are wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from elyon.shared_kernel.edcs.numeric import ZERO, dec

from .order import Fill, OrderRequest, OrderState
from .ports import (
    BrokerAck,
    BrokerError,
    BrokerErrorKind,
    BrokerOrderState,
    Clock,
)


@dataclass(slots=True)
class PaperBroker:
    """An in-memory venue.

    ``fail_place`` and ``accept_despite_failure`` together reproduce the one
    scenario that matters most: the request arrived, the venue accepted it, and
    the *response* was lost. To the OMS that is indistinguishable from the order
    never being placed -- which is exactly why it must ask rather than assume.
    """

    clock: Clock
    orders: dict[str, BrokerOrderState] = field(default_factory=dict)
    placed: list[str] = field(default_factory=list)
    idempotency_keys: set[str] = field(default_factory=set)
    _fill_seq: int = 0

    # Fault injection, per method. Sharing one queue between place and query
    # would make it impossible to express the case that matters most: a venue
    # that will not accept an order but *will* answer what it holds, which is
    # what lets the OMS retry safely instead of halting.
    fail_place: list[BrokerError] = field(default_factory=list)
    fail_query: list[BrokerError] = field(default_factory=list)
    # When True, a failed place still records the order at the venue: the
    # request landed, the answer did not.
    accept_despite_failure: bool = False

    def place(self, request: OrderRequest, idempotency_key: str) -> BrokerAck:
        coid = request.client_order_id

        # A real venue dedupes on the client order id. Honouring that here is
        # what makes a same-id resend safe rather than a second position.
        if coid in self.orders:
            existing = self.orders[coid]
            return BrokerAck(
                broker_order_id=existing.broker_order_id or f"B-{coid[:8]}",
                at_ns=self.clock.now_ns(),
            )

        if self.fail_place:
            error = self.fail_place.pop(0)
            if self.accept_despite_failure:
                self._register(request)
            raise error

        self.idempotency_keys.add(idempotency_key)
        self._register(request)
        return BrokerAck(
            broker_order_id=self.orders[coid].broker_order_id or "",
            at_ns=self.clock.now_ns(),
        )

    def _register(self, request: OrderRequest) -> None:
        coid = request.client_order_id
        self.placed.append(coid)
        self.orders[coid] = BrokerOrderState(
            exists=True,
            broker_order_id=f"B-{len(self.placed):04d}",
            state=OrderState.ACKNOWLEDGED,
            fills=(),
        )

    def query(self, client_order_id: str) -> BrokerOrderState:
        if self.fail_query:
            raise self.fail_query.pop(0)
        return self.orders.get(client_order_id, BrokerOrderState(exists=False))

    def cancel(self, client_order_id: str) -> None:
        if client_order_id in self.orders:
            self.orders[client_order_id] = BrokerOrderState(
                exists=True,
                broker_order_id=self.orders[client_order_id].broker_order_id,
                state=OrderState.CANCELLED,
            )

    # -- driving the venue from a test ------------------------------------

    def fill(
        self, client_order_id: str, quantity: Decimal, price: Decimal
    ) -> Fill:
        """Execute part or all of an order, venue-side."""
        self._fill_seq += 1
        fill = Fill(
            broker_event_id=f"F-{self._fill_seq:06d}",
            quantity=quantity,
            price=price,
            at_ns=self.clock.now_ns(),
        )
        current = self.orders[client_order_id]
        self.orders[client_order_id] = BrokerOrderState(
            exists=True,
            broker_order_id=current.broker_order_id,
            state=OrderState.PARTIALLY_FILLED,
            fills=current.fills + (fill,),
        )
        return fill

    def times_placed(self, client_order_id: str) -> int:
        """How many distinct placements the venue actually saw.

        The number a duplicate-order test is really asking about.
        """
        return self.placed.count(client_order_id)


def timeout(message: str = "no response from venue") -> BrokerError:
    return BrokerError(BrokerErrorKind.TIMEOUT, message)


def rejection(message: str = "insufficient margin") -> BrokerError:
    return BrokerError(BrokerErrorKind.REJECTED, message)


def unavailable(message: str = "venue unreachable") -> BrokerError:
    return BrokerError(BrokerErrorKind.UNAVAILABLE, message)
