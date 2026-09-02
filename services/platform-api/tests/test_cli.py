"""Command line tests.

The CLI is the surface a person actually touches, so what is tested here is
mostly whether it tells the truth: that a refusal explains itself, that a
message never implies something happened when it did not, and that prices
survive the round trip through a file unchanged.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from elyon.cli import main, read_bars
from elyon.modules.backtesting.domain import GeneratorConfig, generate
from elyon.modules.market_data.domain.model import Timeframe
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

MARKET = generate(GeneratorConfig(cycles=20))


@pytest.fixture
def bars_csv(tmp_path: Path) -> Path:
    path = tmp_path / "bars.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "open", "high", "low", "close", "volume"])
        for candle in MARKET:
            writer.writerow([
                candle.open_time_ns, candle.open, candle.high,
                candle.low, candle.close, candle.volume,
            ])
    return path


@pytest.fixture
def session_json(tmp_path: Path) -> Path:
    path = tmp_path / "session.json"
    path.write_text(json.dumps({
        "symbol": "EURUSD",
        "strategies": ["SIX_PILLARS"],
        "warmupBars": 40,
        "atrPeriod": 14,
    }))
    return path


class TestReadingBars:
    def test_prices_survive_the_round_trip_exactly(self, bars_csv: Path):
        # Parsing "1.10005" through a float and back is how a price becomes
        # 1.1000499999999999 and two runs on the same file stop agreeing.
        loaded = read_bars(bars_csv, "EURUSD", Timeframe.M1)
        assert len(loaded) == len(MARKET)
        for original, parsed in zip(MARKET, loaded):
            assert parsed.open == original.open
            assert parsed.close == original.close
            assert parsed.high == original.high
            assert parsed.low == original.low

    def test_a_missing_column_says_which(self, tmp_path: Path):
        path = tmp_path / "bad.csv"
        path.write_text("time,open,high,close\n1,1,1,1\n")
        with pytest.raises(DeterminismError, match="low"):
            read_bars(path, "EURUSD", Timeframe.M1)

    def test_the_error_shows_the_expected_header(self, tmp_path: Path):
        path = tmp_path / "bad.csv"
        path.write_text("a,b\n1,2\n")
        with pytest.raises(DeterminismError, match="Expected header"):
            read_bars(path, "EURUSD", Timeframe.M1)

    def test_an_empty_file_is_refused(self, tmp_path: Path):
        path = tmp_path / "empty.csv"
        path.write_text("time,open,high,low,close\n")
        with pytest.raises(DeterminismError, match="no rows"):
            read_bars(path, "EURUSD", Timeframe.M1)

    def test_a_bad_row_names_the_line(self, tmp_path: Path):
        # A file of 400,000 bars with one broken row needs to say which row.
        path = tmp_path / "bad.csv"
        path.write_text(
            "time,open,high,low,close\n"
            "1700000000,1.1,1.2,1.0,1.15\n"
            "1700000060,1.1,1.0,1.2,1.15\n"   # high below low
        )
        with pytest.raises(DeterminismError, match="line 3"):
            read_bars(path, "EURUSD", Timeframe.M1)

    def test_epochs_are_disambiguated_by_magnitude(self, tmp_path: Path):
        seconds = tmp_path / "s.csv"
        seconds.write_text(
            "time,open,high,low,close\n1700000000,1.1,1.2,1.0,1.15\n"
        )
        nanos = tmp_path / "ns.csv"
        nanos.write_text(
            "time,open,high,low,close\n1700000000000000000,1.1,1.2,1.0,1.15\n"
        )
        a = read_bars(seconds, "EURUSD", Timeframe.M1)[0]
        b = read_bars(nanos, "EURUSD", Timeframe.M1)[0]
        assert a.open_time_ns == b.open_time_ns

    def test_iso_timestamps_are_accepted(self, tmp_path: Path):
        path = tmp_path / "iso.csv"
        path.write_text(
            "time,open,high,low,close\n"
            "2026-01-15T14:00:00+00:00,1.1,1.2,1.0,1.15\n"
        )
        assert len(read_bars(path, "EURUSD", Timeframe.M1)) == 1


class TestCommands:
    def test_strategies_lists_the_catalog(self, capsys):
        assert main(["strategies"]) == 0
        out = capsys.readouterr().out
        assert "SIX_PILLARS" in out or "Six Pillars" in out
        assert "UNPROVEN" in out

    def test_strategies_explains_why_everything_is_unproven(self, capsys):
        main(["strategies"])
        out = capsys.readouterr().out
        assert "hypotheses, not blessings" in out
        assert "elyon calibrate" in out

    def test_dna_lists_every_profile(self, capsys):
        assert main(["dna"]) == 0
        out = capsys.readouterr().out
        for symbol in ("EURUSD", "XAUUSD", "BTCUSD", "NAS100"):
            assert symbol in out

    def test_dna_shows_one_profile(self, capsys):
        assert main(["dna", "XAUUSD"]) == 0
        assert "METAL" in capsys.readouterr().out

    def test_an_unknown_instrument_is_refused_not_invented(self, capsys):
        assert main(["dna", "SOLUSD"]) == 1
        assert "no Market DNA" in capsys.readouterr().err

    def test_config_emits_loadable_json(self, capsys, tmp_path: Path):
        assert main(["config", "--symbol", "XAUUSD"]) == 0
        raw = json.loads(capsys.readouterr().out)
        assert raw["symbol"] == "XAUUSD"

        from elyon.modules.session.domain import SessionConfig
        SessionConfig.from_dict(raw)   # must not raise

    def test_run_reports_where_the_pipeline_stopped(
        self, capsys, session_json: Path, bars_csv: Path
    ):
        code = main(["run", "--config", str(session_json), "--data", str(bars_csv)])
        assert code == 0
        out = capsys.readouterr().out
        assert "where the pipeline stopped" in out
        assert "config hash" in out

    def test_run_warns_that_nothing_will_trade(
        self, capsys, session_json: Path, bars_csv: Path
    ):
        # Nothing is calibrated, so nothing can trade -- and the user is told
        # before the output rather than left to wonder.
        main(["run", "--config", str(session_json), "--data", str(bars_csv)])
        assert "no calibration" in capsys.readouterr().err

    def test_a_missing_file_is_reported_not_traced(self, capsys):
        assert main(["run", "--config", "nope.json", "--data", "nope.csv"]) == 1
        assert "error:" in capsys.readouterr().err


class TestCalibrateTellsTheTruth:
    def test_an_in_sample_run_is_not_certified(self, capsys, bars_csv: Path):
        assert main(["calibrate", "--data", str(bars_csv)]) == 0
        out = capsys.readouterr().out
        assert "not certified" in out
        assert "in-sample" in out

    def test_a_short_sample_says_it_changes_nothing(self, capsys, bars_csv: Path):
        # "Certified" next to an UNPROVEN tier reads as a green light for
        # something that will do nothing, which is worse than saying nothing.
        main(["calibrate", "--data", str(bars_csv), "--sample", "OUT_OF_SAMPLE"])
        out = capsys.readouterr().out
        if "UNPROVEN" in out:
            assert "changes nothing" in out
            assert "Certified as" not in out

    def test_the_report_is_always_shown(self, capsys, bars_csv: Path):
        main(["calibrate", "--data", str(bars_csv)])
        out = capsys.readouterr().out
        assert "expectancy" in out
        assert "ex-best trade" in out


class TestParser:
    def test_a_command_is_required(self):
        with pytest.raises(SystemExit):
            main([])

    def test_run_requires_both_config_and_data(self):
        with pytest.raises(SystemExit):
            main(["run", "--config", "x.json"])
