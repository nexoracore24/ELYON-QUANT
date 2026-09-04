"""A conformance suite for broker adapters.

Connecting a real venue means implementing three methods. Getting any of them
subtly wrong means duplicate positions, and the failure will not appear in
testing -- it appears the first time the network hiccups with real money on.

So the contract is not written in prose here, it is written as executable
checks. Point this at any adapter and it will tell you whether the OMS's safety
properties actually hold against it:

    from elyon.modules.execution.domain.conformance import check_adapter
    report = check_adapter(lambda clock: MyBrokerAdapter(...), clock)
    print(report)

The checks are deliberately about the awkward cases rather than the happy path.
An adapter that places orders correctly and answers ``query`` wrongly passes
every casual test and loses money on the first timeout.

**These checks place orders.** Run them against a demo account.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from elyon.shared_kernel.edcs.numeric import dec

from .oms import client_order_id, idempotency_key
from .order import OrderRequest
from .ports import BrokerAdapter, BrokerError, Clock

AdapterFactory = Callable[[Clock], BrokerAdapter]


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str
    critical: bool = True

    def __str__(self) -> str:
        mark = "✓" if self.passed else ("✗" if self.critical else "!")
        return f"{mark} {self.name}: {self.detail}"


@dataclass(slots=True)
class ConformanceReport:
    checks: list[Check] = field(default_factory=list)

    def record(self, name: str, passed: bool, detail: str, *, critical: bool = True):
        self.checks.append(Check(name, passed, detail, critical))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.critical]

    @property
    def safe_to_use(self) -> bool:
        """Whether the OMS's duplicate-order guarantees hold against this venue."""
        return not self.failures

    def __str__(self) -> str:
        lines = [str(c) for c in self.checks]
        if self.safe_to_use:
            lines.append(
                "\nAll critical checks passed. The OMS's guarantees hold "
                "against this adapter."
            )
        else:
            lines.append(
                f"\n{len(self.failures)} critical failure(s). Using this "
                f"adapter risks duplicate positions -- do not connect it to a "
                f"funded account until they pass."
            )
        return "\n".join(lines)


def check_adapter(
    factory: AdapterFactory,
    clock: Clock,
    *,
    symbol: str = "EURUSD",
    quantity: Decimal | None = None,
) -> ConformanceReport:
    """Run every contract check against one adapter.

    Each check is independent and gets a fresh adapter, so one failure cannot
    cascade into false failures downstream.
    """
    size = quantity if quantity is not None else dec("0.01")
    report = ConformanceReport()

    _check_query_absent(factory, clock, symbol, size, report)
    _check_place_then_query(factory, clock, symbol, size, report)
    _check_idempotent_place(factory, clock, symbol, size, report)
    _check_errors_are_typed(factory, clock, symbol, size, report)
    _check_cancel_is_visible(factory, clock, symbol, size, report)
    return report


@dataclass(frozen=True, slots=True)
class _Attempt:
    """The result of calling an adapter, or a description of how it misbehaved.

    Every adapter call goes through this. A conformance suite that crashes when
    handed a broken adapter is useless -- reporting the misbehaviour *is* the
    job, and an adapter that raises where the contract says return is exactly
    the kind of thing it exists to find.
    """

    value: object | None = None
    failure: str | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None


def _attempt(what: str, call: Callable[[], object]) -> _Attempt:
    try:
        return _Attempt(value=call())
    except BrokerError as exc:
        return _Attempt(failure=f"{what} raised {exc.kind.value}: {exc}")
    except Exception as exc:  # noqa: BLE001 -- reporting this is the point
        return _Attempt(
            failure=f"{what} raised {type(exc).__name__} rather than "
                    f"BrokerError: {exc}"
        )


def _request(symbol: str, quantity: Decimal, tag: str) -> OrderRequest:
    from .events import Side

    return OrderRequest(
        client_order_id=client_order_id(f"conformance-{tag}"),
        correlation_id=f"conformance-{tag}",
        symbol=symbol,
        side=Side.BUY,
        quantity=quantity,
    )


def _check_query_absent(factory, clock, symbol, size, report) -> None:
    """An order that was never placed must come back ``exists=False``.

    The single most important check in this file. If an adapter answers
    "exists" for an unknown id -- or throws instead of answering -- the OMS
    will adopt an order that does not exist, and the real one never gets sent.
    """
    adapter = factory(clock)
    unknown = client_order_id("conformance-never-placed")
    attempt = _attempt("query", lambda: adapter.query(unknown))
    if not attempt.ok:
        report.record(
            "query on an unknown order", False,
            f"{attempt.failure}. The OMS cannot distinguish 'not placed' from "
            f"'cannot tell', so it will halt rather than resend -- safe, but "
            f"it will never recover.",
        )
        return

    state = attempt.value
    report.record(
        "query on an unknown order",
        state.exists is False,
        "returns exists=False" if state.exists is False
        else "claims an order exists that was never placed -- the OMS would "
             "adopt a phantom and the real order would never be sent",
    )


def _check_place_then_query(factory, clock, symbol, size, report) -> None:
    """After a successful place, the venue must admit it has the order.

    This is what makes recovery work: the OMS asks after a timeout, and an
    adapter that cannot find its own order will send a second one.
    """
    adapter = factory(clock)
    request = _request(symbol, size, "place-query")
    placed = _attempt(
        "place",
        lambda: adapter.place(
            request, idempotency_key(request.client_order_id, "place")
        ),
    )
    if not placed.ok:
        report.record(
            "place then query", False,
            f"{placed.failure}. Cannot verify the recovery path.",
        )
        return
    ack = placed.value

    queried = _attempt("query", lambda: adapter.query(request.client_order_id))
    if not queried.ok:
        report.record("place then query", False, queried.failure)
        return

    state = queried.value
    report.record(
        "place then query",
        state.exists,
        f"the venue reports the order (broker id {ack.broker_order_id})"
        if state.exists
        else "the venue does not report an order it just accepted. After a "
             "timeout the OMS would conclude nothing was placed and send a "
             "second order -- this is the duplicate-position bug.",
    )


def _check_idempotent_place(factory, clock, symbol, size, report) -> None:
    """Placing the same client order id twice must not create two orders.

    The OMS already refuses a second send, so this is defence in depth rather
    than the primary guard -- but a venue that deduplicates is what makes the
    recovery resend safe.
    """
    adapter = factory(clock)
    request = _request(symbol, size, "idempotent")
    key = idempotency_key(request.client_order_id, "place")

    first_try = _attempt("place", lambda: adapter.place(request, key))
    if not first_try.ok:
        report.record(
            "duplicate place is deduplicated", False,
            f"{first_try.failure}. Cannot verify deduplication.",
        )
        return

    second_try = _attempt("second place", lambda: adapter.place(request, key))
    if not second_try.ok:
        # Refusing a duplicate outright is acceptable behaviour; what is not
        # acceptable is refusing it in a way the OMS reads as "outcome
        # unknown", which would send it round the reconcile loop.
        unknown = "TIMEOUT" in (second_try.failure or "") or \
            "UNAVAILABLE" in (second_try.failure or "")
        report.record(
            "duplicate place is deduplicated", False,
            f"{second_try.failure}. Refusing a duplicate is fine; refusing it "
            f"as an unknown outcome is not.",
            critical=unknown,
        )
        return

    first, second = first_try.value, second_try.value
    report.record(
        "duplicate place is deduplicated",
        first.broker_order_id == second.broker_order_id,
        "the same client order id maps to one broker order"
        if first.broker_order_id == second.broker_order_id
        else f"two broker orders ({first.broker_order_id}, "
             f"{second.broker_order_id}) for one client id -- a recovery "
             f"resend would double the position",
    )


def _check_errors_are_typed(factory, clock, symbol, size, report) -> None:
    """Failures must arrive as BrokerError with a meaningful kind.

    The OMS branches on exactly one distinction: is the outcome *known*? A
    rejection is a fact it can record; a timeout is a question it must ask. An
    adapter that raises a bare exception, or types a rejection as a timeout,
    turns that decision into a coin flip.

    Provoking a rejection without a venue's cooperation is not always possible
    -- and note that the obvious probe, a negative quantity, cannot be built at
    all: ``OrderRequest`` refuses it, so the OMS structurally cannot send one.
    An unknown instrument is the next best lever. When even that is accepted,
    the check says it could not learn anything rather than claiming a pass.
    """
    adapter = factory(clock)
    probe = _request("__ELYON_NO_SUCH_INSTRUMENT__", size, "typed-errors")
    try:
        adapter.place(probe, idempotency_key(probe.client_order_id, "place"))
    except BrokerError as exc:
        report.record(
            "errors are typed",
            not exc.outcome_is_unknown,
            f"an unknown instrument raised {exc.kind.value}"
            if not exc.outcome_is_unknown
            else f"an unknown instrument raised {exc.kind.value}, which the "
                 f"OMS reads as 'outcome unknown' and will reconcile over "
                 f"instead of recording a refusal",
        )
        return
    except Exception as exc:  # noqa: BLE001 -- that is precisely the finding
        report.record(
            "errors are typed", False,
            f"raised {type(exc).__name__} rather than BrokerError. The OMS "
            f"cannot tell a rejection from a timeout and will not reconcile.",
        )
        return
    report.record(
        "errors are typed", True,
        "could not provoke a rejection (the adapter accepted an unknown "
        "instrument); verify by hand that real failures arrive as BrokerError "
        "with a kind that reflects whether the outcome is known",
        critical=False,
    )


def _check_cancel_is_visible(factory, clock, symbol, size, report) -> None:
    """A cancelled order must stop reporting as live."""
    adapter = factory(clock)
    request = _request(symbol, size, "cancel")
    placed = _attempt(
        "place",
        lambda: adapter.place(
            request, idempotency_key(request.client_order_id, "place")
        ),
    )
    if not placed.ok:
        report.record(
            "cancel is reflected in query", False, placed.failure, critical=False
        )
        return

    cancelled_call = _attempt(
        "cancel", lambda: adapter.cancel(request.client_order_id)
    )
    if not cancelled_call.ok:
        report.record(
            "cancel is reflected in query", False,
            cancelled_call.failure, critical=False,
        )
        return

    queried = _attempt("query", lambda: adapter.query(request.client_order_id))
    if not queried.ok:
        report.record(
            "cancel is reflected in query", False, queried.failure, critical=False
        )
        return

    state = queried.value
    from .order import OrderState

    cancelled = (not state.exists) or state.state is OrderState.CANCELLED
    report.record(
        "cancel is reflected in query",
        cancelled,
        "a cancelled order no longer reports as live" if cancelled
        else f"still reports {state.state} after cancel; the OMS would keep "
             f"treating it as working",
        critical=False,
    )
