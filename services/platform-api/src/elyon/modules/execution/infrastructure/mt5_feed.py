"""Market data from the MetaTrader 5 terminal.

MT5 does not push. There is no callback, no socket to subscribe to -- you ask
``symbol_info_tick`` what the last tick was and it tells you, whether or not
anything has changed since you last asked. So this is a poller, and everything
awkward about it follows from that:

*   **The same tick comes back over and over.** Folding it twice would inflate
    volume and tick counts on the candle, so ticks are deduplicated on their
    own content before anything downstream sees them.
*   **Ticks between two polls are simply gone.** At 250ms you will miss ticks
    in a fast market. That is acceptable *because decisions are taken on
    confirmed candles*: a missed tick can move a high or a low slightly, but it
    cannot change which bar closed or when.
*   **"No tick" and "no connection" look identical.** A weekend and a dead
    socket both return nothing, so the distinction is drawn by asking the
    terminal whether it is still there rather than by inferring it from silence.

MT5 timestamps are seconds (or milliseconds via ``time_msc``). Millisecond
precision is used when available, because two ticks in the same second are
common and collapsing them loses the order they arrived in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from elyon.modules.market_data.domain import Tick
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec

from .mt5 import Mt5Config, _import_mt5


@dataclass(slots=True)
class Mt5TickFeed:
    """Polls one symbol's last tick.

    ``sequence`` is assigned here rather than taken from the terminal: MT5 does
    not number ticks, and the candle builder needs a stable order for ticks
    that share a timestamp.
    """

    symbol: str
    config: Mt5Config = field(default_factory=Mt5Config)
    mt5: Any = None
    provider: str = "mt5"

    _sequence: int = 0
    _last: tuple | None = None
    _closed: bool = False

    def __post_init__(self) -> None:
        if self.mt5 is None:
            self.mt5 = _import_mt5()

    @property
    def venue_symbol(self) -> str:
        return f"{self.symbol}{self.config.symbol_suffix}"

    def poll(self) -> list[Tick]:
        if self._closed:
            raise ConnectionError("feed is closed")

        raw = self.mt5.symbol_info_tick(self.venue_symbol)
        if raw is None:
            # Could be a closed market or a lost terminal. Silence does not
            # distinguish them, so ask.
            raise ConnectionError(self._diagnose())

        signature = (
            int(getattr(raw, "time_msc", 0) or getattr(raw, "time", 0)),
            float(raw.bid),
            float(raw.ask),
        )
        if signature == self._last:
            return []  # the terminal repeated itself; nothing new happened
        self._last = signature

        return [self._to_tick(raw)]

    def _to_tick(self, raw: Any) -> Tick:
        self._sequence += 1

        # time_msc is milliseconds; time is seconds. Two ticks inside one
        # second are ordinary, and collapsing them loses their order.
        millis = int(getattr(raw, "time_msc", 0) or 0)
        if millis:
            event_ns = millis * 1_000_000
        else:
            event_ns = int(raw.time) * 1_000_000_000

        # str() before dec(): the terminal hands out doubles, and going
        # through Decimal(float) would carry the float's error into a value the
        # whole engine then treats as exact.
        return Tick(
            symbol=self.symbol,
            event_time_ns=event_ns,
            bid=dec(str(raw.bid)),
            ask=dec(str(raw.ask)),
            provider=self.provider,
            seq=self._sequence,
            volume=dec(str(getattr(raw, "volume", 0) or 0)),
        )

    def _diagnose(self) -> str:
        """Say which kind of nothing this is."""
        try:
            if not self.mt5.terminal_info():
                return "the MT5 terminal is not reachable"
            info = self.mt5.symbol_info(self.venue_symbol)
            if info is None:
                return (
                    f"the terminal does not know {self.venue_symbol!r}. Check "
                    f"the account's symbol suffix -- Exness Standard and Cent "
                    f"accounts append one (EURUSDm), Pro and Raw do not."
                )
            if not getattr(info, "visible", True):
                return (
                    f"{self.venue_symbol} is not in Market Watch. Add it in the "
                    f"terminal, or the API will keep returning nothing."
                )
        except Exception as exc:  # noqa: BLE001
            return f"terminal query failed: {exc}"
        return f"no tick available for {self.venue_symbol} (market may be closed)"

    def close(self) -> None:
        self._closed = True

    def ensure_symbol(self) -> None:
        """Make the symbol visible, or say why it cannot be.

        A symbol missing from Market Watch returns None from every price call,
        which is indistinguishable from a dead connection until somebody
        checks. Better to fail at startup with the reason.
        """
        info = self.mt5.symbol_info(self.venue_symbol)
        if info is None:
            raise DeterminismError(
                f"{self.venue_symbol!r} is unknown to the terminal. Exness "
                f"Standard and Cent accounts append a suffix (EURUSDm); Pro "
                f"and Raw do not. Set Mt5Config(symbol_suffix=...)."
            )
        if not getattr(info, "visible", True):
            if not self.mt5.symbol_select(self.venue_symbol, True):
                raise DeterminismError(
                    f"could not add {self.venue_symbol} to Market Watch"
                )


def build_feed(symbol: str, **config) -> Mt5TickFeed:
    return Mt5TickFeed(symbol, Mt5Config(**config))
