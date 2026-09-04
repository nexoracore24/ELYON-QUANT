"""Order Management System.

Every execution goes through the OMS. An order placed outside it has no event
log, no idempotency key and no reconciliation, so nobody can say afterwards what
happened or why.

State is a fold over an immutable event log, so a process that dies mid-send
comes back and knows exactly what it had already done. And the rule that keeps
one intended position from becoming two: **a send that times out has an unknown
outcome**, so the OMS asks the broker before it ever resends.
"""

from .events import EventKind, OrderEvent, OrderType, Side, TimeInForce
from .oms import (
    Oms,
    OmsConfig,
    SafeHalt,
    SendOutcome,
    client_order_id,
    idempotency_key,
)
from .order import (
    TERMINAL_STATES,
    TRANSITIONS,
    Fill,
    IllegalTransition,
    Order,
    OrderRequest,
    OrderState,
)
from .paper import PaperBroker, rejection, timeout, unavailable
from .ports import (
    BrokerAck,
    BrokerAdapter,
    BrokerError,
    BrokerErrorKind,
    BrokerOrderState,
    Clock,
    ManualClock,
)
from .conformance import Check, ConformanceReport, check_adapter
from .store import (
    CorruptLog,
    EventStore,
    InMemoryEventStore,
    JsonlEventStore,
    LoadedLog,
)
from .resilience import (
    BreakerState,
    CircuitBreaker,
    DeadLetter,
    DeadLetterQueue,
    Outbox,
    OutboxEntry,
)

__all__ = [
    "BreakerState", "BrokerAck", "Check", "ConformanceReport", "check_adapter", "CorruptLog", "EventStore",
    "InMemoryEventStore", "JsonlEventStore", "LoadedLog", "BrokerAdapter", "BrokerError",
    "BrokerErrorKind", "BrokerOrderState", "CircuitBreaker", "Clock",
    "DeadLetter", "DeadLetterQueue", "EventKind", "Fill", "IllegalTransition",
    "ManualClock", "Oms", "OmsConfig", "Order", "OrderEvent", "OrderRequest",
    "OrderState", "OrderType", "Outbox", "OutboxEntry", "PaperBroker",
    "SafeHalt", "SendOutcome", "Side", "TERMINAL_STATES", "TRANSITIONS",
    "TimeInForce", "client_order_id", "idempotency_key", "rejection",
    "timeout", "unavailable",
]
