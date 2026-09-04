"""Getting bars out of the terminal.

Two of these matter more than the rest: the forming bar must never be
exported, and prices must not arrive through a float. Both failures are
invisible in the output -- a file with a forming bar looks like a file, and
1.1000499999999999 looks like a rounding display issue right up until two runs
over the same data stop agreeing.
"""

from __future__ import annotations

import pytest

from elyon.modules.execution.infrastructure.mt5 import Mt5Config
from elyon.modules.execution.infrastructure.mt5_history import (
    MT5_TIMEFRAMES,
    Mt5History,
)
from elyon.modules.market_data.domain.model import CandleState, Timeframe
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

HOUR = 3600


class FakeInfo:
    def __init__(self, digits: int = 5) -> None:
        self.digits = digits
        self.visible = True


class FakeTerminal:
    """A terminal that answers like MT5 does, floats and all."""

    def __init__(self, *, digits: int = 5, known: bool = True) -> None:
        self.digits = digits
        self.known = known
        self.calls: list[tuple] = []
        # Position 0 is the bar being built right now. Its high is a lie: it
        # will move before the bar closes.
        self.rows = [
            {"time": 1_700_000_000 + i * 300, "open": 1.10005 + i / 100000,
             "high": 1.10025 + i / 100000, "low": 1.09995 + i / 100000,
             "close": 1.10015 + i / 100000, "tick_volume": 100 + i}
            for i in range(50)
        ]

    def symbol_info(self, symbol):
        return FakeInfo(self.digits) if self.known else None

    def copy_rates_from_pos(self, symbol, period, start, count):
        self.calls.append((symbol, period, start, count))
        # Newest first in the real API's indexing sense: position 0 is the
        # live bar, so a start of 1 skips it.
        return self.rows[start:start + count]


def a_history(**kwargs) -> Mt5History:
    terminal = kwargs.pop("terminal", None) or FakeTerminal()
    suffix = kwargs.pop("suffix", "")
    return Mt5History(
        kwargs.pop("symbol", "EURUSD"),
        Mt5Config(symbol_suffix=suffix),
        mt5=terminal,
        **kwargs,
    )


class TestTheFormingBar:
    def test_it_is_never_exported(self):
        # The bar being built has a high and a low that change before it
        # closes. Loading it as history is the no-repaint violation the whole
        # engine exists to avoid.
        terminal = FakeTerminal()
        a_history(terminal=terminal).candles(Timeframe.M5, 10)
        _, _, start, _ = terminal.calls[0]
        assert start == 1

    def test_every_exported_candle_is_confirmed(self):
        series = a_history().candles(Timeframe.M5, 10)
        assert all(c.state is CandleState.CONFIRMED for c in series)


class TestPrices:
    def test_they_go_through_the_symbols_own_precision(self):
        # The terminal knows how many digits the symbol has; a float's repr
        # does not. This is the difference between 1.10005 and
        # 1.1000499999999999.
        # Index 1 of the fixture: the forming bar at index 0 is skipped.
        series = a_history().candles(Timeframe.M5, 1)
        assert series[0].open == dec("1.10006")
        assert str(series[0].open) == "1.10006"

    def test_a_two_digit_instrument_is_not_given_five(self):
        # Gold quotes to 2 decimals. Formatting it to 5 would invent precision
        # the venue does not have.
        terminal = FakeTerminal(digits=2)
        series = a_history(terminal=terminal, symbol="XAUUSD").candles(
            Timeframe.M5, 1
        )
        assert str(series[0].open) == "1.10"

    def test_no_price_arrives_as_a_float(self):
        series = a_history().candles(Timeframe.M5, 5)
        for candle in series:
            for value in (candle.open, candle.high, candle.low, candle.close):
                assert not isinstance(value, float)


class TestTheServerClock:
    def test_the_default_assumes_utc(self):
        series = a_history().candles(Timeframe.M5, 1)
        assert series[0].open_time_ns == 1_700_000_300 * 1_000_000_000

    def test_an_offset_shifts_the_bars_back_onto_utc(self):
        # A server running at UTC+3 stamps 10:00 on a bar that happened at
        # 07:00 UTC. Without the correction every killzone moves three hours
        # and nothing looks wrong.
        series = a_history(server_offset_hours=3).candles(Timeframe.M5, 1)
        assert series[0].open_time_ns == (1_700_000_300 - 3 * HOUR) * 1_000_000_000

    def test_a_nonsense_offset_is_refused(self):
        with pytest.raises(DeterminismError, match="not a real timezone"):
            a_history(server_offset_hours=25)

    def test_the_window_is_described_in_utc(self):
        # The one mistake that matters here is invisible in the numbers, so a
        # person has to be shown timestamps they can check against a session
        # they know.
        history = a_history()
        series = history.candles(Timeframe.M5, 5)
        described = history.describe_window(series)
        assert "UTC" in described
        assert "5 bars" in described


class TestTheSymbol:
    def test_the_suffix_reaches_the_terminal(self):
        terminal = FakeTerminal()
        history = a_history(terminal=terminal, suffix="m")
        history.candles(Timeframe.M5, 5)
        assert history.venue_symbol == "EURUSDm"
        assert terminal.calls[0][0] == "EURUSDm"

    def test_the_candles_carry_the_plain_symbol(self):
        # The venue's suffix is an account detail. Letting it into the domain
        # would mean every strategy had to know which account was opened.
        series = a_history(suffix="m").candles(Timeframe.M5, 1)
        assert series[0].symbol == "EURUSD"

    def test_an_unknown_symbol_suggests_the_suffix(self):
        terminal = FakeTerminal(known=False)
        with pytest.raises(DeterminismError, match="suffix"):
            a_history(terminal=terminal).candles(Timeframe.M5, 5)


class TestRefusals:
    def test_an_empty_answer_says_what_to_do_about_it(self):
        # MT5 only downloads history for charts it has been asked to show,
        # which is the single most common cause of "it returned nothing".
        terminal = FakeTerminal()
        terminal.rows = []
        with pytest.raises(DeterminismError, match="scroll back"):
            a_history(terminal=terminal).candles(Timeframe.M5, 5)

    def test_asking_for_no_candles_is_refused(self):
        with pytest.raises(DeterminismError, match="at least one"):
            a_history().candles(Timeframe.M5, 0)

    def test_every_supported_timeframe_maps_to_a_terminal_constant(self):
        for timeframe in Timeframe:
            assert timeframe in MT5_TIMEFRAMES


class TestTheFile:
    def test_it_is_in_the_shape_the_engine_reads(self, tmp_path):
        from elyon.cli import read_bars

        path = tmp_path / "bars.csv"
        written = a_history().to_csv(path, Timeframe.M5, 20)
        assert written == 20

        # The real proof: the loader accepts it, unchanged.
        series = read_bars(path, "EURUSD", Timeframe.M5)
        assert len(series) == 20
        assert series[0].open == dec("1.10006")

    def test_the_header_is_first(self, tmp_path):
        path = tmp_path / "bars.csv"
        a_history().to_csv(path, Timeframe.M5, 5)
        assert path.read_text().splitlines()[0] == \
            "time,open,high,low,close,volume"

    def test_writing_is_atomic(self, tmp_path):
        path = tmp_path / "bars.csv"
        a_history().to_csv(path, Timeframe.M5, 5)
        assert [p.name for p in tmp_path.iterdir()] == ["bars.csv"]

    def test_a_round_trip_preserves_the_prices_exactly(self, tmp_path):
        from elyon.cli import read_bars

        path = tmp_path / "bars.csv"
        exported = a_history().candles(Timeframe.M5, 30)
        a_history().to_csv(path, Timeframe.M5, 30)
        loaded = read_bars(path, "EURUSD", Timeframe.M5)
        assert [c.close for c in loaded] == [c.close for c in exported]
        assert [c.open_time_ns for c in loaded] == \
            [c.open_time_ns for c in exported]
