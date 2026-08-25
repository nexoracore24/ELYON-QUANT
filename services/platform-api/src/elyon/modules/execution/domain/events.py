"""The order event log.

Everything the OMS knows is a fold over these. Nothing is modified in place and
nothing is deleted, which is what makes the guarantee in ENG-006 §0.1 real:
given the same log, the OMS reaches bit-identical state. A process that crashes
mid-send comes back, replays, and knows exactly what it had already done.

Two properties every event here holds:

*   **It is a fact, not an intention.** ``OrderSent`` means "we wrote down that
    we are about to send", and it is persisted *before* the network call. An
    event written after the I/O would be lost by exactly the crash it exists to
    survive.
*   **It carries its own identity.** Broker events dedupe on
    ``broker_event_id``, so the same fill arriving twice is applied once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from elyon.shared_kernel.edcs.canonical import canonical_decimal, data_hash
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    DAY = "DAY"


class EventKind(str, Enum):
    """Every fact the log can record."""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    RISK_APPROVED = "RISK_APPROVED"
    QUEUED = "QUEUED"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    RECONCILED = "RECONCILED"
    SAFE_HALTED = "SAFE_HALTED"
    RESUMED = "RESUMED"
    CLOSED = "CLOSED"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """One immutable fact about one order.

    ``sequence`` is assigned by the store, not the producer: an event's position
    in the log is a property of the log, and letting a caller choose it is how
    two events end up claiming the same slot.
    """

    kind: EventKind
    client_order_id: str
    at_ns: int
    sequence: int = 0
    # Present only on events that originated at the broker. Deduplication keys
    # on it, so a redelivered execution report is applied exactly once.
    broker_event_id: str | None = None
    quantity: Decimal = ZERO
    price: Decimal = ZERO
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quantity < ZERO:
            raise DeterminismError(
                f"{self.kind.value} carries negative quantity {self.quantity}"
            )
        if self.price < ZERO:
            raise DeterminismError(
                f"{self.kind.value} carries negative price {self.price}"
            )

    @property
    def from_broker(self) -> bool:
        return self.broker_event_id is not None

    def to_canonical_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind.value,
            "clientOrderId": self.client_order_id,
            "at": self.at_ns,
            "sequence": self.sequence,
            "quantity": canonical_decimal(self.quantity),
            "price": canonical_decimal(self.price),
            "reason": self.reason,
        }
        if self.broker_event_id is not None:
            out["brokerEventId"] = self.broker_event_id
        if self.payload:
            out["payload"] = {k: str(v) for k, v in sorted(self.payload.items())}
        return out

    @property
    def event_hash(self) -> str:
        return data_hash(self.to_canonical_dict())

    def __str__(self) -> str:
        parts = [f"#{self.sequence}", self.kind.value]
        if self.quantity > ZERO:
            parts.append(f"qty {self.quantity}")
        if self.price > ZERO:
            parts.append(f"@ {self.price}")
        if self.reason:
            parts.append(f"({self.reason})")
        return " ".join(parts)
