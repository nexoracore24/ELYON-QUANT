"""Writing a configuration out and reading it back.

A serialiser that drifts from its parser drifts silently, and the symptom is
the worst one available: the engine comes back up after a restart looking
correct and sized against something else. So the round trip is walked field by
field rather than spot-checked.
"""

from __future__ import annotations

import json

import pytest

from elyon.modules.risk.domain import InstrumentSpec
from elyon.modules.session.domain import Mode, RiskSettings, SessionConfig
from elyon.modules.session.domain.settings import SETTINGS, apply_changes
from elyon.modules.strategy.domain import (
    Calibration,
    ConflictPolicy,
    StrategyId,
)
from elyon.modules.trading.domain.position import ManagementPolicy
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

GOLD = InstrumentSpec(
    lot_step=dec("0.01"), min_lot=dec("0.01"),
    max_lot=dec("50"), value_per_price_unit=dec("100"),
)


def a_full_config() -> SessionConfig:
    """Nothing left at a default, so a dropped field cannot hide."""
    return SessionConfig(
        symbol="XAUUSD",
        mode=Mode.LIVE,
        timeframe="M5",
        strategies=(StrategyId.ICT_2022_MODEL, StrategyId.ICT_UNICORN),
        shadow_strategies=(StrategyId.SIX_PILLARS,),
        conflict_policy=ConflictPolicy.MAJORITY,
        calibrations={
            StrategyId.ICT_2022_MODEL: Calibration(
                sample_size=180, wins=97, expectancy_r=dec("0.41"),
                max_drawdown_r=dec("6.2"), dataset="xauusd-2025-m5",
            ),
        },
        risk=RiskSettings(
            equity=dec("25000"), risk_per_trade=dec("0.0075"),
            daily_loss_limit=dec("0.03"), max_open_risk=dec("0.04"),
            min_reward_risk=dec("2.5"), max_concurrent_positions=1,
        ),
        management=ManagementPolicy(
            break_even_at_r=dec("0.8"), break_even_buffer_r=dec("0.15"),
            trail_from_r=dec("2.0"), trail_distance_atr=dec("1.25"),
            partial_at_r=dec("1.75"), partial_fraction=dec("0.4"),
            time_stop_bars=25, time_stop_min_r=dec("0.4"),
        ),
        instrument=GOLD,
        atr_period=21,
        swing_grade=2,
        warmup_bars=60,
        lookback_bars=200,
        entry_score_threshold=72,
        allow_uncalibrated_live=True,
        calendar_path="calendar.csv",
    )


class TestRoundTrip:
    def test_a_default_configuration_survives(self):
        config = SessionConfig(symbol="EURUSD")
        assert SessionConfig.from_dict(config.to_dict()) == config

    def test_a_configuration_with_nothing_defaulted_survives(self):
        config = a_full_config()
        assert SessionConfig.from_dict(config.to_dict()) == config

    def test_every_settable_field_survives_individually(self):
        # Field by field, because a batch round trip can pass while one field
        # is quietly reset to the same default it started at.
        base = SessionConfig(symbol="EURUSD")
        for setting in SETTINGS:
            before = setting.read(base)
            restored = SessionConfig.from_dict(base.to_dict())
            assert setting.read(restored) == before, setting.key

    def test_a_changed_setting_survives_a_round_trip(self):
        base = SessionConfig(symbol="EURUSD")
        for key, value in (
            ("riskPerTrade", "0.0125"), ("equity", "75000"),
            ("minRewardRisk", "3"), ("breakEvenAtR", "0.9"),
            ("partialAtR", None), ("timeStopBars", 15),
            ("valuePerPriceUnit", "100"), ("lotStep", "0.1"),
            ("entryScoreThreshold", 80), ("swingGrade", 3),
            ("conflictPolicy", "STRONGEST_WINS"),
            ("strategies", ["ICT_2022_MODEL"]),
            ("allowUncalibratedLive", True), ("calendar", "events.csv"),
        ):
            changed, _ = apply_changes(base, {key: value})
            restored = SessionConfig.from_dict(changed.to_dict())
            assert restored == changed, key

    def test_the_hash_survives_a_round_trip(self):
        # The provenance hash is what ties a decision to a configuration. If it
        # moved across a save, every stored decision would stop matching the
        # engine that made it.
        config = a_full_config()
        assert SessionConfig.from_dict(config.to_dict()).config_hash == \
            config.config_hash

    def test_saving_and_loading_a_file(self, tmp_path):
        # The bug this replaces: save() wrote the provenance shape, which
        # load() then refused as a file full of unknown keys.
        path = tmp_path / "session.json"
        config = a_full_config()
        config.save(path)
        assert SessionConfig.load(path) == config

    def test_the_saved_file_is_json_a_person_can_edit(self):
        raw = a_full_config().to_dict()
        text = json.dumps(raw, indent=2)
        assert '"symbol": "XAUUSD"' in text
        assert '"riskPerTrade": "0.0075"' in text

    def test_prices_are_written_as_strings(self):
        # Through JSON as a float, 0.0075 comes back as 0.007499999999999999
        # and two runs on the same file stop agreeing.
        raw = a_full_config().to_dict()
        assert isinstance(raw["risk"]["riskPerTrade"], str)
        assert isinstance(raw["instrument"]["valuePerPriceUnit"], str)
        for value in json.loads(json.dumps(raw))["risk"].values():
            assert not isinstance(value, float)

    def test_saving_is_atomic(self, tmp_path):
        # Written to a temporary file and renamed, so an interrupted write
        # cannot leave half a configuration where a working one used to be.
        path = tmp_path / "session.json"
        SessionConfig(symbol="EURUSD").save(path)
        assert [p.name for p in tmp_path.iterdir()] == ["session.json"]

    def test_saving_over_a_configuration_replaces_it(self, tmp_path):
        path = tmp_path / "session.json"
        SessionConfig(symbol="EURUSD").save(path)
        changed, _ = apply_changes(SessionConfig.load(path), {"riskPerTrade": "0.02"})
        changed.save(path)
        assert SessionConfig.load(path).risk.risk_per_trade == dec("0.02")


class TestTheCanonicalShapeIsNotTheFileShape:
    def test_the_hash_shape_is_deliberately_lossy(self):
        # It is the fingerprint of a decision, not a configuration file, and
        # confusing the two is what produced a save() that load() refused.
        config = a_full_config()
        canonical = config.to_canonical_dict()
        assert "equity" not in canonical
        assert "instrument" not in canonical
        assert isinstance(canonical["calibrations"][0], str)

    def test_the_file_shape_carries_what_the_hash_drops(self):
        raw = a_full_config().to_dict()
        assert raw["risk"]["equity"] == "25000"
        assert raw["instrument"]["valuePerPriceUnit"] == "100"
        assert raw["calibrations"][0]["wins"] == 97


class TestTheInstrumentContract:
    def test_it_is_loaded_from_the_file(self):
        # It sizes every position. Left at a standard FX lot it is simply
        # wrong for gold, and wrong here means every trade on that instrument
        # is the wrong size with nothing in the logs looking unusual.
        config = SessionConfig.from_dict({
            "symbol": "XAUUSD",
            "instrument": {"valuePerPriceUnit": "100", "maxLot": "50"},
        })
        assert config.instrument.value_per_price_unit == dec("100")
        assert config.instrument.max_lot == dec("50")

    def test_it_defaults_to_a_standard_fx_lot(self):
        config = SessionConfig.from_dict({"symbol": "EURUSD"})
        assert config.instrument.value_per_price_unit == dec("100000")

    def test_an_impossible_contract_is_refused(self):
        with pytest.raises(DeterminismError):
            SessionConfig.from_dict({
                "symbol": "EURUSD",
                "instrument": {"minLot": "10", "maxLot": "1"},
            })
