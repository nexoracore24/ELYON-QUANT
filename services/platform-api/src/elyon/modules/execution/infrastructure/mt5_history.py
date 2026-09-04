"""Getting bars out of the MetaTrader 5 terminal.

Everything else assumes a CSV of confirmed candles already exists. This is
where it comes from, and three things about MT5 make it less trivial than it
looks.

**The most recent bar is still forming.** ``copy_rates_from_pos`` starting at
position 0 hands back the bar that is being built right now, with a high and a
low that will change before it closes. Writing that to a file and then loading
it as history is the no-repaint violation the whole engine is built to avoid --
so this starts at position 1 and the current bar is never exported.

**Prices arrive as doubles.** The terminal knows how many digits the symbol
has; a float's repr does not. Formatting each price to the symbol's own
precision is the difference between ``1.10005`` and ``1.1000499999999999``, and
the second one makes two runs over the same file disagree.

**The timestamps are the broker's clock, not UTC.** This is the trap that costs
the most and shows nothing when it goes wrong. Every ICT model in the catalog is
defined by a window in New York local time; if the broker's server runs at UTC+3
and the bars are read as UTC, every killzone is silently three hours off and the
engine looks like it is working. There is no way to ask MT5 what its server
offset is, so this refuses to guess: it states what it assumed, and takes a
correction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from elyon.modules.market_data.domain.model import Candle, CandleState, Timeframe
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

from .mt5 import Mt5Config, _import_mt5

SECONDS_PER_HOUR = 3600

# MT5's own timeframe constants. Hard-coded rather than read off the module so
# the mapping is reviewable, and so a test can drive this without the package.
MT5_TIMEFRAMES: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.H1: 16385,
    Timeframe.H4: 16388,
    Timeframe.D1: 16408,
}


@dataclass(slots=True)
class Mt5History:
    """Reads closed candles from the terminal.

    ``server_offset_hours`` shifts the terminal's timestamps onto real UTC.
    Most brokers run their server clock at UTC+2 in winter and UTC+3 in summer;
    Exness servers are typically UTC+0. Getting it wrong does not fail, it just
    moves every killzone, so it is a value someone sets rather than one this
    class invents.
    """

    symbol: str
    config: Mt5Config = field(default_factory=Mt5Config)
    server_offset_hours: int = 0
    mt5: Any = None

    def __post_init__(self) -> None:
        if self.mt5 is None:
            self.mt5 = _import_mt5()
        if not -14 <= self.server_offset_hours <= 14:
            raise DeterminismError(
                f"server offset of {self.server_offset_hours}h is not a real "
                f"timezone"
            )

    @property
    def venue_symbol(self) -> str:
        return f"{self.symbol}{self.config.symbol_suffix}"

    # -- reading ----------------------------------------------------------

    def digits(self) -> int:
        """How many decimal places this symbol quotes to, per the terminal."""
        info = self.mt5.symbol_info(self.venue_symbol)
        if info is None:
            raise DeterminismError(
                f"the terminal does not know {self.venue_symbol!r}. Exness "
                f"Standard and Cent accounts append a suffix (EURUSDm); Pro "
                f"and Raw do not. Set Mt5Config(symbol_suffix=...)."
            )
        return int(getattr(info, "digits", 5))

    def candles(self, timeframe: Timeframe, count: int) -> CandleSeries:
        """The last ``count`` **closed** candles, oldest first."""
        if count < 1:
            raise DeterminismError("ask for at least one candle")

        period = MT5_TIMEFRAMES.get(timeframe)
        if period is None:
            known = ", ".join(t.value for t in MT5_TIMEFRAMES)
            raise DeterminismError(
                f"{timeframe.value} is not one the terminal serves; known: {known}"
            )

        # Position 1, not 0. Position 0 is the bar being built right now.
        rows = self.mt5.copy_rates_from_pos(
            self.venue_symbol, period, 1, count
        )
        if rows is None or len(rows) == 0:
            raise DeterminismError(
                f"the terminal returned no history for {self.venue_symbol} on "
                f"{timeframe.value}. Open the symbol's chart in the terminal "
                f"and scroll back -- MT5 only downloads history for charts it "
                f"has been asked to show."
            )

        digits = self.digits()
        shift_ns = -self.server_offset_hours * SECONDS_PER_HOUR * 1_000_000_000
        return CandleSeries.of([
            self._to_candle(row, timeframe, digits, shift_ns) for row in rows
        ])

    def _to_candle(
        self, row: Any, timeframe: Timeframe, digits: int, shift_ns: int
    ) -> Candle:
        open_ns = int(row["time"]) * 1_000_000_000 + shift_ns

        def price(field_name: str):
            # Formatted to the symbol's own precision before it becomes a
            # Decimal. Going through Decimal(float) would carry the double's
            # error into a value the whole engine then treats as exact.
            return dec(f"{float(row[field_name]):.{digits}f}")

        return Candle(
            symbol=self.symbol,
            timeframe=timeframe,
            open_time_ns=open_ns,
            close_time_ns=open_ns + timeframe.duration_ns,
            open=price("open"), high=price("high"),
            low=price("low"), close=price("close"),
            # tick_volume is the count of price changes; real_volume is only
            # populated on exchange-traded instruments and is 0 on most FX.
            volume=dec(str(int(row["tick_volume"]))),
            tick_count=int(row["tick_volume"]),
            state=CandleState.CONFIRMED,
        )

    # -- writing ----------------------------------------------------------

    def to_csv(self, path: str | Path, timeframe: Timeframe, count: int) -> int:
        """Write the history in the shape ``elyon run`` reads. Returns rows."""
        series = self.candles(timeframe, count)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        lines = ["time,open,high,low,close,volume"]
        lines.extend(
            f"{c.open_time_ns // 1_000_000_000},{c.open},{c.high},{c.low},"
            f"{c.close},{c.volume}"
            for c in series
        )
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text("\n".join(lines) + "\n")
        temporary.replace(target)
        return len(series)

    def describe_window(self, series: CandleSeries) -> str:
        """What was exported, in a form somebody can sanity-check.

        Printed rather than logged, because the one mistake that matters here
        is invisible in the numbers: bars stamped in the wrong clock look
        perfectly normal. Seeing the first and last timestamp in UTC is how a
        person notices that "the London session" is landing at the wrong hour.
        """
        def stamp(ns: int) -> str:
            return datetime.fromtimestamp(
                ns / 1_000_000_000, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")

        return (
            f"{len(series)} bars, {stamp(series[0].open_time_ns)} → "
            f"{stamp(series[-1].open_time_ns)}"
        )
