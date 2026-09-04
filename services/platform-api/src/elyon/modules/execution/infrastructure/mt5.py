"""MetaTrader 5 adapter, for Exness and any other MT5 broker.

Read this before connecting anything.

**MT5 has no client order id.** ``order_send`` takes a ``magic`` number and a
short ``comment``, and neither is a deduplicating key: send the same request
twice and you get two positions. Every other venue this OMS was designed
against will refuse a duplicate ``client_order_id``; MT5 will happily fill it.

That removes one of the three defences against duplicate positions, and it is
worth being precise about which:

*   The state machine still holds -- ``QUEUED`` is the only path to ``SENT``, so
    the OMS cannot send twice by itself.
*   Query-before-resend still holds, and now carries the whole weight. The OMS
    only ever resends after the venue has said the order does not exist.
*   Venue-side deduplication is **gone**. It was defence in depth, and on MT5
    there is no depth.

So ``query`` is not merely important here, it is the only thing standing between
a timed-out send and a doubled position. This adapter therefore searches for an
order in four places -- pending orders, open positions, today's deals, and
recent history -- because an order that filled and closed while the connection
was down still has to be found.

Two residual risks that no code here can remove, stated plainly:

1.  **The in-flight window.** If ``order_send`` times out and the order is still
    being processed, ``query`` may not see it yet, and the OMS would resend.
    :attr:`Mt5Config.settle_seconds` waits before the first query to shrink that
    window; it cannot close it.
2.  **The ``magic``/``comment`` tag is how orders are recognised.** If anything
    else trades this account under the same magic number, this adapter will
    mistake those trades for its own.

**Requirements.** The ``MetaTrader5`` package is Windows-only and needs the MT5
terminal installed, running, logged in, with algorithmic trading enabled. It is
imported lazily so the rest of the platform runs anywhere.

**Exness specifics.** Symbols usually carry a suffix that depends on the account
type (``EURUSDm`` on Standard/Cent, plain ``EURUSD`` on Pro/Raw). Set
:attr:`Mt5Config.symbol_suffix` rather than renaming things elsewhere: the
strategy layer should not know what account you opened.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

from elyon.shared_kernel.edcs.canonical import sha256_hex
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec

from ..domain.events import OrderType, Side
from ..domain.order import Fill, OrderRequest, OrderState
from ..domain.ports import (
    BrokerAck,
    BrokerError,
    BrokerErrorKind,
    BrokerOrderState,
    Clock,
)

# MT5 return codes. Grouped by what the OMS needs to know: is the outcome a
# fact, or a question?
#
# VERIFY THESE against your terminal's documentation before trading. They are
# transcribed here for readability, and a wrong number in this table is a wrong
# decision about whether to reconcile. The *structure* is the part that matters
# and is safe: anything not listed is treated as an unknown outcome.
RETCODE_DONE = 10009
RETCODE_PLACED = 10008
RETCODE_DONE_PARTIAL = 10010

SUCCESS_CODES = frozenset({RETCODE_DONE, RETCODE_PLACED, RETCODE_DONE_PARTIAL})

# Refusals: the venue said no, and that is information the OMS can record.
REJECTION_CODES: Mapping[int, str] = {
    10004: "requote",
    10006: "request rejected",
    10011: "request processing error",
    10013: "invalid request",
    10014: "invalid volume",
    10015: "invalid price",
    10016: "invalid stops",
    10017: "trading disabled",
    10018: "market closed",
    10019: "insufficient funds",
    10020: "prices changed",
    10021: "no quotes to process the request",
    10022: "invalid order expiration",
    10024: "too many requests",
    10027: "autotrading disabled in the terminal",
    10028: "autotrading disabled by the server",
    10030: "unsupported filling mode",
    10031: "no connection to the trade server",
}

# Malformed in a way retrying cannot fix.
INVALID_CODES = frozenset({10013, 10014, 10015, 10016, 10022, 10030})

# Explicitly unknown: the request may or may not have been applied.
UNKNOWN_CODES = frozenset({10012, 10025, 10026})  # timeout, cancelled, locked

MAGIC_MAX = 2_147_483_647
COMMENT_MAX = 31


@dataclass(frozen=True, slots=True)
class Mt5Config:
    """How this adapter talks to one account."""

    # Exness account types append a suffix to every symbol. Configured here so
    # the strategy layer never has to know which account you opened.
    symbol_suffix: str = ""
    deviation_points: int = 20
    # Every order this adapter places carries this magic number, and query only
    # ever looks at orders carrying it. Two systems sharing one magic on one
    # account will each mistake the other's trades for their own.
    magic: int = 20260101
    # How long to let an order settle before the first query after a timeout.
    # Shrinks the in-flight window; cannot close it.
    settle_seconds: float = 1.0
    # How far back to search history when looking for an order that may have
    # filled and closed while the connection was down.
    history_lookback_seconds: int = 24 * 3600

    def __post_init__(self) -> None:
        if not 0 < self.magic <= MAGIC_MAX:
            raise DeterminismError(
                f"magic must be in (0, {MAGIC_MAX}], got {self.magic}"
            )
        if self.settle_seconds < 0:
            raise DeterminismError("settle_seconds cannot be negative")


def order_tag(client_order_id: str) -> str:
    """A short marker MT5 can carry in an order comment.

    MT5 comments hold about 31 characters and a UUID is 36, so the id is
    hashed rather than truncated: truncation invites collisions between orders
    whose ids share a prefix, and a collision here means adopting the wrong
    order.
    """
    return f"EQ{sha256_hex(client_order_id)[:20]}"


@dataclass(slots=True)
class Mt5Adapter:
    """Anti-corruption layer around the MetaTrader 5 terminal.

    Nothing above this class knows what a retcode is, and nothing in it makes a
    trading decision. Its whole job is to answer three questions truthfully,
    especially the one about whether an order exists.
    """

    clock: Clock
    config: Mt5Config = field(default_factory=Mt5Config)
    # Injected for testing; left None the real terminal module is imported.
    mt5: Any = None

    def __post_init__(self) -> None:
        if self.mt5 is None:
            self.mt5 = _import_mt5()

    # -- placing ----------------------------------------------------------

    def place(self, request: OrderRequest, idempotency_key: str) -> BrokerAck:
        payload = self._to_mt5_request(request)
        result = self.mt5.order_send(payload)

        if result is None:
            # No structured answer at all. The request may have been applied.
            code, message = self._last_error()
            raise BrokerError(
                BrokerErrorKind.TIMEOUT,
                f"order_send returned nothing ({code}: {message}); the order "
                f"may or may not exist at the venue",
            )

        retcode = int(getattr(result, "retcode", -1))
        if retcode in SUCCESS_CODES:
            return BrokerAck(
                broker_order_id=str(getattr(result, "order", "") or
                                    getattr(result, "deal", "")),
                at_ns=self.clock.now_ns(),
            )

        raise self._classify(retcode, getattr(result, "comment", ""))

    def _classify(self, retcode: int, comment: str) -> BrokerError:
        """Turn a retcode into the one distinction the OMS acts on.

        An unrecognised code is treated as an **unknown** outcome rather than a
        rejection. Getting that backwards is the dangerous direction: calling an
        applied order "rejected" leaves a position nobody is tracking, whereas
        calling a rejection "unknown" costs one wasted query.
        """
        if retcode in UNKNOWN_CODES:
            return BrokerError(
                BrokerErrorKind.TIMEOUT,
                f"retcode {retcode} ({comment}): outcome unknown",
            )
        if retcode in INVALID_CODES:
            return BrokerError(
                BrokerErrorKind.INVALID,
                f"retcode {retcode}: {REJECTION_CODES.get(retcode, comment)}",
            )
        if retcode in REJECTION_CODES:
            kind = (
                BrokerErrorKind.THROTTLED if retcode == 10024
                else BrokerErrorKind.UNAVAILABLE if retcode == 10031
                else BrokerErrorKind.REJECTED
            )
            return BrokerError(kind, f"retcode {retcode}: {REJECTION_CODES[retcode]}")

        return BrokerError(
            BrokerErrorKind.TIMEOUT,
            f"unrecognised retcode {retcode} ({comment}); treating the outcome "
            f"as unknown so the OMS reconciles rather than assuming a refusal",
        )

    def _to_mt5_request(self, request: OrderRequest) -> dict[str, Any]:
        symbol = f"{request.symbol}{self.config.symbol_suffix}"
        is_buy = request.side is Side.BUY

        if request.order_type is OrderType.MARKET:
            action = self.mt5.TRADE_ACTION_DEAL
            order_type = (
                self.mt5.ORDER_TYPE_BUY if is_buy else self.mt5.ORDER_TYPE_SELL
            )
        else:
            action = self.mt5.TRADE_ACTION_PENDING
            order_type = (
                self.mt5.ORDER_TYPE_BUY_LIMIT if is_buy
                else self.mt5.ORDER_TYPE_SELL_LIMIT
            )

        payload: dict[str, Any] = {
            "action": action,
            "symbol": symbol,
            "volume": float(request.quantity),
            "type": order_type,
            "deviation": self.config.deviation_points,
            "magic": self.config.magic,
            "comment": order_tag(request.client_order_id)[:COMMENT_MAX],
            "type_time": self.mt5.ORDER_TIME_GTC,
        }
        # float() at the boundary is unavoidable -- the terminal API takes
        # doubles -- and it is confined to here. Every decision upstream was
        # made in Decimal, and nothing downstream reads these back as truth:
        # fills come from the venue.
        if request.limit_price is not None:
            payload["price"] = float(request.limit_price)
        if request.stop_loss is not None:
            payload["sl"] = float(request.stop_loss)
        if request.take_profit is not None:
            payload["tp"] = float(request.take_profit)
        return payload

    # -- asking -----------------------------------------------------------

    def query(self, client_order_id: str) -> BrokerOrderState:
        """Find an order, wherever it ended up.

        Four places, and all four are necessary. A pending order sits in
        ``orders_get``; a filled one becomes a position; a position that was
        closed is only in the deal history. Searching fewer places means
        reporting ``exists=False`` for an order that does exist, which on MT5 --
        with no venue-side deduplication -- means the OMS resends and doubles
        the position.
        """
        tag = order_tag(client_order_id)

        pending = self._find_pending(tag)
        if pending is not None:
            return pending

        position = self._find_position(tag)
        if position is not None:
            return position

        historic = self._find_in_history(tag)
        if historic is not None:
            return historic

        return BrokerOrderState(exists=False)

    def settle(self) -> None:
        """Pause before querying after a timeout, to let an order land."""
        if self.config.settle_seconds:
            time.sleep(self.config.settle_seconds)

    def _find_pending(self, tag: str) -> BrokerOrderState | None:
        for order in self._safe_call(self.mt5.orders_get) or ():
            if self._tagged(order, tag):
                return BrokerOrderState(
                    exists=True,
                    broker_order_id=str(order.ticket),
                    state=OrderState.ACKNOWLEDGED,
                )
        return None

    def _find_position(self, tag: str) -> BrokerOrderState | None:
        for position in self._safe_call(self.mt5.positions_get) or ():
            if self._tagged(position, tag):
                return BrokerOrderState(
                    exists=True,
                    broker_order_id=str(position.ticket),
                    state=OrderState.FILLED,
                    fills=(
                        Fill(
                            broker_event_id=f"POS-{position.ticket}",
                            quantity=dec(str(position.volume)),
                            price=dec(str(position.price_open)),
                            at_ns=int(position.time) * 1_000_000_000,
                        ),
                    ),
                )
        return None

    def _find_in_history(self, tag: str) -> BrokerOrderState | None:
        """An order that filled and closed while nobody was watching."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        since = now - timedelta(seconds=self.config.history_lookback_seconds)
        deals = self._safe_call(
            self.mt5.history_deals_get, since, now + timedelta(minutes=1)
        ) or ()

        matched = [d for d in deals if self._tagged(d, tag)]
        if not matched:
            return None

        return BrokerOrderState(
            exists=True,
            broker_order_id=str(matched[0].order),
            state=OrderState.FILLED,
            fills=tuple(
                Fill(
                    broker_event_id=f"DEAL-{deal.ticket}",
                    quantity=dec(str(deal.volume)),
                    price=dec(str(deal.price)),
                    at_ns=int(deal.time) * 1_000_000_000,
                )
                for deal in matched
            ),
        )

    def _tagged(self, record: Any, tag: str) -> bool:
        """Is this one of ours?

        Both the magic number and the comment must match. Magic alone would
        claim every order this system ever placed; comment alone would trust a
        field the terminal is allowed to rewrite.
        """
        return (
            int(getattr(record, "magic", -1)) == self.config.magic
            and tag in str(getattr(record, "comment", ""))
        )

    # -- cancelling -------------------------------------------------------

    def cancel(self, client_order_id: str) -> None:
        state = self._find_pending(order_tag(client_order_id))
        if state is None:
            return  # nothing pending; either filled or never existed

        result = self.mt5.order_send({
            "action": self.mt5.TRADE_ACTION_REMOVE,
            "order": int(state.broker_order_id or 0),
        })
        if result is None:
            code, message = self._last_error()
            raise BrokerError(
                BrokerErrorKind.TIMEOUT, f"cancel returned nothing ({code}: {message})"
            )
        retcode = int(getattr(result, "retcode", -1))
        if retcode not in SUCCESS_CODES:
            raise self._classify(retcode, getattr(result, "comment", ""))

    # -- plumbing ---------------------------------------------------------

    def _safe_call(self, call, *args):
        """Terminal calls return None on failure rather than raising.

        Treating that None as "nothing found" would be the duplicate-position
        bug wearing a disguise: a failed lookup is not an empty result, and
        ``query`` must never report ``exists=False`` because it could not look.
        """
        try:
            result = call(*args)
        except Exception as exc:  # noqa: BLE001 -- the terminal raises anything
            raise BrokerError(
                BrokerErrorKind.UNAVAILABLE, f"{call.__name__} failed: {exc}"
            ) from exc
        if result is None:
            code, message = self._last_error()
            raise BrokerError(
                BrokerErrorKind.UNAVAILABLE,
                f"{call.__name__} returned nothing ({code}: {message}); the "
                f"lookup failed and must not be read as 'no such order'",
            )
        return result

    def _last_error(self) -> tuple[int, str]:
        try:
            code, message = self.mt5.last_error()
            return int(code), str(message)
        except Exception:  # noqa: BLE001
            return -1, "unavailable"


def _import_mt5():
    try:
        import MetaTrader5  # type: ignore
    except ImportError as exc:
        import platform

        system = platform.system()
        if system != "Windows":
            # Naming the platform matters here. "Install the package" sends
            # someone on Linux round a loop that has no exit: the package
            # drives a running Windows terminal through a Windows API, and
            # there is no build for anything else.
            raise DeterminismError(
                f"MetaTrader5 is Windows-only and this host is {system}. "
                f"There is no Linux or macOS build -- the package drives a "
                f"running MT5 terminal through a Windows API. Backtesting, "
                f"calibration and paper trading work here; a live session "
                f"needs a Windows machine with the terminal open. Run "
                f"`elyon doctor` for the full picture."
            ) from exc
        raise DeterminismError(
            "the MetaTrader5 package is not installed. It is Windows-only and "
            "needs the MT5 terminal running and logged in, with algorithmic "
            "trading enabled. Install with: pip install MetaTrader5"
        ) from exc
    return MetaTrader5


def connect(
    *,
    login: int,
    password: str,
    server: str,
    terminal_path: str | None = None,
) -> None:
    """Log the terminal in.

    Credentials are parameters, never fields on the adapter and never written
    anywhere. Read them from the environment or a secrets manager at the call
    site; a password on an object ends up in a repr, a log line or a traceback
    sooner or later.
    """
    mt5 = _import_mt5()
    kwargs: dict[str, Any] = {
        "login": login, "password": password, "server": server
    }
    if terminal_path:
        kwargs["path"] = terminal_path

    if not mt5.initialize(**kwargs):
        code, message = mt5.last_error()
        raise DeterminismError(
            f"MT5 login failed ({code}: {message}). Check that the terminal is "
            f"running, the server name matches exactly (Exness servers look "
            f"like 'Exness-MT5Trial7'), and algorithmic trading is enabled."
        )


def build(clock: Clock, **kwargs) -> Mt5Adapter:
    """Factory for `elyon conformance --adapter elyon...mt5:build`."""
    return Mt5Adapter(clock, Mt5Config(**kwargs))
