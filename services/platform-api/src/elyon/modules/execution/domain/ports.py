"""The boundary between the OMS and everything unreliable.

The OMS core is agnostic of broker and of mode (ADR-EXE-6): the same state
machine runs live, on paper and in a backtest, and only the adapter changes.
That is not tidiness -- it means the code that decides whether to retry, adopt
or fail is the *same code* in a backtest as in production, so the backtest
actually tests it.

Everything on this side of the boundary can fail, time out, lie, or answer
late, and the OMS is written assuming it will.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol

from .order import Fill, OrderRequest, OrderState


class BrokerErrorKind(str, Enum):
    """Why a broker call failed, which decides what to do about it.

    The distinction that matters most is TIMEOUT versus the rest: a rejection is
    a fact, but a timeout means *we do not know* -- and the whole
    duplicate-order problem lives in that gap.
    """

    TIMEOUT = "TIMEOUT"          # unknown outcome; never blind-retry
    REJECTED = "REJECTED"        # a fact: the broker refused
    UNAVAILABLE = "UNAVAILABLE"  # transport down
    THROTTLED = "THROTTLED"
    INVALID = "INVALID"          # malformed; retrying will not help


class BrokerError(Exception):
    def __init__(self, kind: BrokerErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind

    @property
    def outcome_is_unknown(self) -> bool:
        """Whether the order might exist at the broker despite the failure."""
        return self.kind in (BrokerErrorKind.TIMEOUT, BrokerErrorKind.UNAVAILABLE)

    @property
    def is_retryable(self) -> bool:
        return self.kind is not BrokerErrorKind.INVALID


@dataclass(frozen=True, slots=True)
class BrokerAck:
    """The broker admitting it has the order."""

    broker_order_id: str
    at_ns: int


@dataclass(frozen=True, slots=True)
class BrokerOrderState:
    """What the broker says about an order when asked.

    ``exists=False`` is the only answer that makes a resend safe.
    """

    exists: bool
    broker_order_id: str | None = None
    state: OrderState | None = None
    fills: tuple[Fill, ...] = ()

    @property
    def filled_quantity(self) -> Decimal:
        from elyon.shared_kernel.edcs.numeric import ZERO
        return sum((f.quantity for f in self.fills), ZERO)


class Clock(Protocol):
    """Injected so a backtest reproduces the same timings, and therefore the
    same retry and timeout decisions, as the live path."""

    def now_ns(self) -> int: ...


@dataclass(slots=True)
class ManualClock:
    """A clock a test drives, which is the only kind a deterministic test can use."""

    at: int = 0

    def now_ns(self) -> int:
        return self.at

    def advance(self, nanos: int) -> int:
        self.at += nanos
        return self.at


class BrokerAdapter(Protocol):
    """The anti-corruption layer around one venue.

    ``query`` is not optional and not an optimisation. It is the only thing
    standing between a timed-out send and a duplicate position.
    """

    def place(self, request: OrderRequest, idempotency_key: str) -> BrokerAck: ...

    def query(self, client_order_id: str) -> BrokerOrderState: ...

    def cancel(self, client_order_id: str) -> None: ...
