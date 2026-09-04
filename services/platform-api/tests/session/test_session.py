"""Trading session tests.

This is where the engines stop being separate and become a system, so the tests
are about the seams rather than about any one engine:

*   Nothing dangerous is a default -- LIVE and a disabled context gate have to
    be asked for by name.
*   Every bar records *where* the pipeline stopped, because "no trade" is eight
    different answers and a session that cannot tell them apart cannot be
    debugged.
*   The stages agree with each other, or the session stands down. The bug this
    caught in development was a long order whose stop sat above its entry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elyon.modules.backtesting.domain import GeneratorConfig, generate
from elyon.modules.market_context.domain import learn_dna, profile_for
from elyon.modules.session.domain import (
    BarOutcome,
    Mode,
    RiskSettings,
    SessionConfig,
    TradingSession,
)
from elyon.modules.strategy.domain import (
    Calibration,
    ConflictPolicy,
    StrategyId,
)
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec

MARKET = generate(GeneratorConfig(cycles=60))
PROVEN = {
    StrategyId.SIX_PILLARS: Calibration(180, 92, dec("0.42"), dataset="test")
}


def config(**kwargs) -> SessionConfig:
    settings = dict(symbol="EURUSD", calibrations=PROVEN)
    settings.update(kwargs)
    return SessionConfig(**settings)


def run_session(cfg: SessionConfig | None = None) -> TradingSession:
    cfg = cfg or config()
    dna = learn_dna(MARKET, profile_for(cfg.symbol))
    session = TradingSession(cfg, dna=dna)
    for candle in MARKET:
        session._on_candle(candle)
    return session


SESSION = run_session()


class TestNothingDangerousIsADefault:
    def test_paper_is_the_default_mode(self):
        # A system where the dangerous mode is what you get by forgetting to
        # choose will eventually trade real money by accident.
        assert config().mode is Mode.PAPER

    def test_live_has_to_be_named(self):
        assert Mode.PAPER.touches_real_money is False
        assert Mode.LIVE.touches_real_money is True

    def test_the_context_gate_cannot_be_skipped_live(self):
        with pytest.raises(DeterminismError, match="cannot be skipped in LIVE"):
            config(mode=Mode.LIVE, skip_context_gate=True)

    def test_it_can_be_skipped_for_research(self):
        config(mode=Mode.BACKTEST, skip_context_gate=True)   # must not raise

    def test_an_absurd_risk_per_trade_is_refused(self):
        # Above 5% a normal losing streak is an account blow-up, not a drawdown.
        with pytest.raises(DeterminismError, match="outside"):
            RiskSettings(risk_per_trade=dec("0.20"))

    def test_a_daily_limit_below_one_trade_is_refused(self):
        with pytest.raises(DeterminismError, match="first loss would"):
            RiskSettings(risk_per_trade=dec("0.01"), daily_loss_limit=dec("0.005"))

    def test_a_session_with_no_strategies_is_refused(self):
        with pytest.raises(DeterminismError, match="evaluate nothing"):
            config(strategies=())

    def test_a_strategy_cannot_be_live_and_shadow_at_once(self):
        with pytest.raises(DeterminismError, match="one or the other"):
            config(
                strategies=(StrategyId.SIX_PILLARS,),
                shadow_strategies=(StrategyId.SIX_PILLARS,),
            )


class TestWarningsAreHandedOver:
    def test_an_uncalibrated_live_strategy_is_reported(self):
        # A warning nobody is handed is a warning nobody reads.
        warnings = config(calibrations={}).warnings()
        assert any("no calibration" in w for w in warnings)

    def test_the_warning_says_it_means_no_trades(self):
        warnings = config(calibrations={}).warnings()
        assert any("take no trades" in w for w in warnings)

    def test_a_calibrated_session_still_flags_the_missing_calendar(self):
        # Calibration silences the tier warning; the absent news feed is a
        # separate gap and stays visible.
        warnings = config().warnings()
        assert not any("no calibration" in w for w in warnings)
        assert any("no economic calendar" in w for w in warnings)

    def test_the_calendar_warning_names_the_ceiling(self):
        # Eight points that cannot be earned is worth saying out loud.
        assert any("92/100" in w for w in config().warnings())

    def test_a_fully_configured_session_warns_about_nothing(self):
        assert config(calendar_path="calendar.csv").warnings() == ()

    def test_live_mode_says_so(self):
        assert any("real broker" in w for w in config(mode=Mode.LIVE).warnings())

    def test_a_skipped_gate_says_so(self):
        warnings = config(skip_context_gate=True).warnings()
        assert any("any market condition" in w for w in warnings)


class TestThePipelineReportsWhereItStopped:
    def test_every_bar_produces_an_outcome(self):
        assert len(SESSION.outcomes) == len(MARKET)

    def test_every_outcome_names_a_stage_and_a_reason(self):
        for outcome in SESSION.outcomes:
            assert outcome.stopped_at
            assert outcome.reason.strip()

    def test_the_stages_are_countable(self):
        counts = SESSION.stopped_at_counts()
        assert sum(counts.values()) == len(SESSION.outcomes)
        assert "warmup" in counts

    def test_warmup_bars_are_not_evaluated(self):
        early = [o for o in SESSION.outcomes[:10]]
        assert all(o.stopped_at == "warmup" for o in early)

    def test_the_summary_says_where_to_look(self):
        # The most useful output when a session is not trading.
        summary = SESSION.summary()
        assert "where the pipeline stopped" in summary
        assert "entries taken" in summary


class TestTheStagesMustAgree:
    def test_no_position_is_opened_with_incoherent_geometry(self):
        # The bug this caught: the geometry comes from the six-pillar setup
        # while the side comes from the playbook, and when those disagree the
        # stop is computed for one direction and the order placed in the other.
        for position in SESSION.closed_positions:
            if position.direction.name == "UP":
                assert position.initial_stop < position.entry < position.target
            else:
                assert position.target < position.entry < position.initial_stop

    def test_an_incoherent_setup_is_recorded_not_traded(self):
        rejected = [
            o for o in SESSION.outcomes
            if o.stopped_at == "risk" and "incoherent geometry" in o.reason
        ]
        for outcome in rejected:
            assert not outcome.traded


class TestOnePositionAtATime:
    def test_a_new_entry_is_not_sought_while_holding(self):
        # Looking for entries while in a trade is how a session ends up with
        # more risk on than it decided to take.
        holding = False
        for outcome in SESSION.outcomes:
            if outcome.traded:
                assert not holding
                holding = True
            elif outcome.stopped_at == "management":
                assert holding
                if outcome.management and outcome.management.position.closed:
                    holding = False

    def test_every_entry_eventually_closes_or_is_still_open(self):
        entries = sum(1 for o in SESSION.outcomes if o.traded)
        assert len(SESSION.closed_positions) <= entries
        still_open = 1 if SESSION.position is not None else 0
        assert len(SESSION.closed_positions) + still_open == entries


class TestExecution:
    def test_every_entry_produced_exactly_one_order(self):
        traded = [o for o in SESSION.outcomes if o.traded]
        assert len(SESSION.oms.orders) == len(traded)

    def test_order_ids_are_unique(self):
        ids = [o.order_id for o in SESSION.outcomes if o.traded]
        assert len(set(ids)) == len(ids)

    def test_the_oms_is_not_halted(self):
        assert not SESSION.oms.is_halted

    def test_a_decision_record_accompanies_every_entry(self):
        for outcome in SESSION.outcomes:
            if outcome.traded:
                assert outcome.decision is not None
                assert outcome.decision.provenance.config_hash


class TestDeterminism:
    def test_the_same_data_produces_the_same_session(self):
        a, b = run_session(), run_session()
        assert [o.stopped_at for o in a.outcomes] == [o.stopped_at for o in b.outcomes]
        assert a.realized_r == b.realized_r

    def test_the_config_hash_is_stable(self):
        assert config().config_hash == config().config_hash

    def test_changing_risk_changes_the_hash(self):
        # Otherwise a replay cannot tell which settings produced a trade.
        base = config()
        other = config(risk=RiskSettings(risk_per_trade=dec("0.01")))
        assert base.config_hash != other.config_hash

    def test_the_config_hash_travels_into_every_decision(self):
        cfg = config()
        session = run_session(cfg)
        for outcome in session.outcomes:
            if outcome.decision is not None:
                assert outcome.decision.provenance.config_hash == cfg.config_hash


class TestConfigFiles:
    def test_a_minimal_file_loads(self):
        cfg = SessionConfig.from_dict({"symbol": "EURUSD"})
        assert cfg.symbol == "EURUSD"
        assert cfg.mode is Mode.PAPER

    def test_an_unknown_key_fails_loudly(self):
        # A typo that silently leaves a setting at its default is a bug that
        # surfaces as unexplained behaviour weeks later.
        with pytest.raises(DeterminismError, match="unknown configuration key"):
            SessionConfig.from_dict({"symbol": "EURUSD", "riskPerTrade": 0.01})

    def test_the_error_lists_what_is_valid(self):
        with pytest.raises(DeterminismError, match="skipContextGate"):
            SessionConfig.from_dict({"symbol": "EURUSD", "nonsense": 1})

    def test_a_missing_symbol_fails(self):
        with pytest.raises(DeterminismError, match="must name a symbol"):
            SessionConfig.from_dict({})

    def test_an_unknown_strategy_fails_with_the_list(self):
        with pytest.raises(DeterminismError, match="ICT_2022_MODEL"):
            SessionConfig.from_dict({"symbol": "EURUSD", "strategies": ["SIX_PILARS"]})

    def test_nested_settings_load(self):
        cfg = SessionConfig.from_dict({
            "symbol": "XAUUSD",
            "strategies": ["SIX_PILLARS", "ICT_TURTLE_SOUP"],
            "risk": {"equity": "50000", "riskPerTrade": "0.0025"},
            "management": {"breakEvenAtR": "1.2", "partialAtR": None},
        })
        assert cfg.risk.equity == dec("50000")
        assert cfg.management.break_even_at_r == dec("1.2")
        assert cfg.management.partial_at_r is None
        assert len(cfg.strategies) == 2

    def test_a_config_round_trips_through_disk(self, tmp_path: Path):
        cfg = config()
        target = tmp_path / "session.json"
        cfg.save(target)
        assert json.loads(target.read_text())["symbol"] == "EURUSD"


class TestCalibrationsInConfig:
    """The link that closes the loop: `calibrate` prints, config consumes."""

    def test_a_calibration_block_loads(self):
        cfg = SessionConfig.from_dict({
            "symbol": "EURUSD",
            "calibrations": [{
                "strategy": "SIX_PILLARS", "sampleSize": 180, "wins": 92,
                "expectancyR": "0.42", "dataset": "eurusd-2024",
            }],
        })
        from elyon.modules.strategy.domain import ProbabilityTier
        assert cfg.playbook().tier_of(StrategyId.SIX_PILLARS) \
            is ProbabilityTier.HIGH
        assert not any("no calibration" in w for w in cfg.warnings())

    def test_the_config_supplies_a_sample_not_a_tier(self):
        # A configuration file cannot claim a tier it did not earn: it gives
        # the numbers, and the same rules derive the tier everywhere.
        cfg = SessionConfig.from_dict({
            "symbol": "EURUSD",
            "calibrations": [{
                "strategy": "SIX_PILLARS", "sampleSize": 200, "wins": 180,
                "expectancyR": "-0.30",
            }],
        })
        from elyon.modules.strategy.domain import ProbabilityTier
        # 90% win rate, negative expectancy -> LOW, whatever the file wanted.
        assert cfg.playbook().tier_of(StrategyId.SIX_PILLARS) \
            is ProbabilityTier.LOW

    def test_a_short_sample_still_leaves_it_unproven(self):
        cfg = SessionConfig.from_dict({
            "symbol": "EURUSD",
            "calibrations": [{
                "strategy": "SIX_PILLARS", "sampleSize": 12, "wins": 11,
                "expectancyR": "2.5",
            }],
        })
        assert any("no calibration" in w for w in cfg.warnings())

    def test_an_incomplete_block_says_what_is_missing(self):
        with pytest.raises(DeterminismError, match="expectancyR"):
            SessionConfig.from_dict({
                "symbol": "EURUSD",
                "calibrations": [{"strategy": "SIX_PILLARS", "sampleSize": 100,
                                  "wins": 50}],
            })

    def test_an_unknown_strategy_is_refused(self):
        with pytest.raises(DeterminismError, match="unknown strategy"):
            SessionConfig.from_dict({
                "symbol": "EURUSD",
                "calibrations": [{"strategy": "MADE_UP", "sampleSize": 100,
                                  "wins": 50, "expectancyR": "0.4"}],
            })

    def test_the_sample_changes_the_provenance_hash(self):
        # A session running on 180 trades of evidence is not the same session
        # as one running on 40, and a replay has to tell them apart.
        def with_sample(size: int) -> SessionConfig:
            return SessionConfig.from_dict({
                "symbol": "EURUSD",
                "calibrations": [{"strategy": "SIX_PILLARS",
                                  "sampleSize": size, "wins": size // 2,
                                  "expectancyR": "0.42"}],
            })

        assert with_sample(180).config_hash != with_sample(40).config_hash


class TestRegistryWiring:
    def test_live_and_shadow_reach_the_registry(self):
        cfg = config(
            strategies=(StrategyId.SIX_PILLARS,),
            shadow_strategies=(StrategyId.ICT_2022_MODEL,),
        )
        registry = cfg.registry()
        assert registry.live_ids == (StrategyId.SIX_PILLARS,)
        assert registry.shadow_ids == (StrategyId.ICT_2022_MODEL,)

    def test_calibrations_reach_the_playbook(self):
        from elyon.modules.strategy.domain import ProbabilityTier
        playbook = config().playbook()
        assert playbook.tier_of(StrategyId.SIX_PILLARS) is ProbabilityTier.HIGH

    def test_the_conflict_policy_is_carried_through(self):
        cfg = config(conflict_policy=ConflictPolicy.MAJORITY)
        assert cfg.playbook().conflict_policy is ConflictPolicy.MAJORITY
