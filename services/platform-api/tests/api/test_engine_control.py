"""Configuring and starting a running engine.

The interesting cases here are all refusals. Applying a setting is easy; the
value of this surface is that it says no to the changes that would quietly
corrupt something -- a symbol swapped under accumulated history, an equity
figure restating a position already sized, LIVE mode reached by a stray tap.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from elyon.modules.api.domain import (
    LIVE_CONFIRMATION,
    LoginService,
    OperatorStore,
    PasswordHasher,
    Role,
    Router,
    TokenRegistry,
    control_for,
    panel_for,
    preflight,
)
from elyon.modules.session.domain import Mode, SessionConfig, TradingSession
from elyon.modules.session.domain.settings import (
    BY_KEY,
    SETTINGS,
    Scope,
    apply_changes,
    changed_keys,
    describe,
)
from elyon.modules.strategy.domain import Calibration, StrategyId
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

GOOD = "correct-horse-battery"
PROVEN = Calibration(
    sample_size=180, wins=95, expectancy_r=dec("0.35"), dataset="eurusd-h1"
)


def a_session(**overrides) -> TradingSession:
    return TradingSession(SessionConfig(symbol="EURUSD", **overrides))


class FakePosition:
    """Just enough of a position for the flat-only rules to see one."""

    direction = None
    quantity = Decimal("1")


# ---------------------------------------------------------------------------
# The settings table
# ---------------------------------------------------------------------------

class TestTheSettingsTable:
    def test_every_setting_round_trips_through_its_own_pair(self):
        # A reader and a writer that disagree would show one value and apply
        # another, which is the worst possible failure for a settings screen.
        config = SessionConfig(symbol="EURUSD")
        for setting in SETTINGS:
            value = setting.read(config)
            rebuilt = setting.write(config, value)
            assert setting.read(rebuilt) == value, setting.key

    def test_every_setting_declares_a_scope(self):
        # Adding a setting without deciding when it may be changed is the
        # mistake this table exists to prevent.
        for setting in SETTINGS:
            assert isinstance(setting.scope, Scope)
            assert setting.help

    def test_unknown_keys_are_refused(self):
        with pytest.raises(DeterminismError, match="unknown setting"):
            apply_changes(SessionConfig(symbol="EURUSD"), {"riskPerTrde": "0.01"})

    def test_nothing_is_mutated_when_a_change_is_invalid(self):
        config = SessionConfig(symbol="EURUSD")
        with pytest.raises(DeterminismError):
            apply_changes(config, {"riskPerTrade": "0.9"})   # above the 5% cap
        assert config.risk.risk_per_trade == dec("0.005")

    def test_a_partially_invalid_batch_applies_none_of_it(self):
        # The valid half must not land. A configuration is valid as a whole or
        # it is not a configuration.
        config = SessionConfig(symbol="EURUSD")
        with pytest.raises(DeterminismError):
            apply_changes(
                config, {"riskPerTrade": "0.01", "conflictPolicy": "NONSENSE"}
            )
        assert config.risk.risk_per_trade == dec("0.005")

    def test_an_unchanged_value_is_not_reported_as_a_change(self):
        config = SessionConfig(symbol="EURUSD")
        _, touched = apply_changes(config, {"riskPerTrade": "0.005"})
        assert touched == ()

    def test_a_decimal_arrives_exactly(self):
        # Through str() rather than float(), or the number the engine uses
        # stops being the number the person typed.
        config, _ = apply_changes(
            SessionConfig(symbol="EURUSD"), {"riskPerTrade": "0.00125"}
        )
        assert config.risk.risk_per_trade == dec("0.00125")

    def test_an_unknown_strategy_names_the_known_ones(self):
        with pytest.raises(DeterminismError, match="SIX_PILLARS"):
            apply_changes(
                SessionConfig(symbol="EURUSD"), {"strategies": ["NOT_A_MODEL"]}
            )

    def test_a_string_is_not_a_strategy_list(self):
        # Otherwise "SIX_PILLARS" silently becomes thirteen single characters.
        with pytest.raises(DeterminismError, match="must be a list"):
            apply_changes(
                SessionConfig(symbol="EURUSD"), {"strategies": "SIX_PILLARS"}
            )

    def test_describe_reports_the_current_value(self):
        config = SessionConfig(symbol="EURUSD")
        entries = {e["key"]: e for e in describe(config)}
        assert entries["symbol"]["value"] == "EURUSD"
        assert entries["mode"]["value"] == "PAPER"
        assert entries["mode"]["dangerous"] is True


# ---------------------------------------------------------------------------
# Reconfiguring a running session
# ---------------------------------------------------------------------------

class TestReconfiguring:
    def test_a_live_setting_takes_effect(self):
        session = a_session()
        candidate, _ = apply_changes(session.config, {"riskPerTrade": "0.01"})
        assert session.reconfigure(candidate) == ("riskPerTrade",)
        assert session.config.risk.risk_per_trade == dec("0.01")

    def test_the_symbol_cannot_change_under_accumulated_history(self):
        session = a_session()
        candidate, _ = apply_changes(session.config, {"symbol": "GBPUSD"})
        with pytest.raises(DeterminismError, match="belong to the current value"):
            session.reconfigure(candidate)
        assert session.config.symbol == "EURUSD"

    @pytest.mark.parametrize("key,value", [
        ("timeframe", "M5"), ("atrPeriod", 21), ("warmupBars", 80),
    ])
    def test_every_restart_setting_is_refused_on_a_running_session(self, key, value):
        session = a_session()
        candidate, _ = apply_changes(session.config, {key: value})
        with pytest.raises(DeterminismError, match="cannot change on a running"):
            session.reconfigure(candidate)

    def test_equity_cannot_change_while_a_position_is_open(self):
        # Editing it would not resize the trade; it would only make every
        # figure reported about it wrong.
        session = a_session()
        session._position = FakePosition()
        candidate, _ = apply_changes(session.config, {"equity": "50000"})
        with pytest.raises(DeterminismError, match="restate a decision"):
            session.reconfigure(candidate)

    def test_the_mode_cannot_change_while_a_position_is_open(self):
        session = a_session()
        session._position = FakePosition()
        candidate, _ = apply_changes(session.config, {"mode": "LIVE"})
        with pytest.raises(DeterminismError, match="position is open"):
            session.reconfigure(candidate)

    def test_risk_per_trade_can_change_while_a_position_is_open(self):
        # It only sizes the *next* trade, so it does not restate this one.
        session = a_session()
        session._position = FakePosition()
        candidate, _ = apply_changes(session.config, {"riskPerTrade": "0.01"})
        assert session.reconfigure(candidate) == ("riskPerTrade",)

    def test_the_budget_is_rebuilt_when_equity_changes(self):
        # The budget holds absolute amounts. Leaving it alone would keep
        # enforcing limits derived from an equity figure nobody uses any more.
        from elyon.modules.risk.domain import Dimension

        session = a_session()
        before = session._budget.available(Dimension.DAILY_LOSS)
        candidate, _ = apply_changes(session.config, {"equity": "50000"})
        session.reconfigure(candidate)
        assert session._budget.available(Dimension.DAILY_LOSS) == before * 5

    def test_an_identical_configuration_changes_nothing(self):
        session = a_session()
        assert session.reconfigure(session.config) == ()

    def test_an_invalid_configuration_never_reaches_the_session(self):
        session = a_session()
        with pytest.raises(DeterminismError):
            # Daily loss below the risk of one trade: the first loss would end
            # the day. Caught while the candidate is being built.
            apply_changes(session.config, {"dailyLossLimit": "0.001"})
        assert session.config.risk.daily_loss_limit == dec("0.02")


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

class TestPreflight:
    def test_an_uncalibrated_paper_session_may_start(self):
        # Quiet is not the same as wrong. Running uncalibrated in PAPER is
        # exactly how a strategy earns a tier.
        report = preflight(a_session())
        assert report.can_start
        assert any("uncalibrated" in a.detail for a in report.advisories)

    def test_a_live_session_with_no_evidence_may_not(self):
        session = a_session(mode=Mode.LIVE)
        report = preflight(session)
        assert not report.can_start
        assert any("uncalibrated" in b.detail for b in report.blockers)

    def test_live_against_a_paper_broker_is_blocked(self):
        session = a_session(
            mode=Mode.LIVE, calibrations={StrategyId.SIX_PILLARS: PROVEN}
        )
        report = preflight(session)
        assert any("orders would be simulated" in b.detail
                   for b in report.blockers)

    def test_a_dead_feed_blocks_a_live_session(self):
        session = a_session(
            mode=Mode.LIVE, calibrations={StrategyId.SIX_PILLARS: PROVEN}
        )
        report = preflight(session, feed_state="DISCONNECTED")
        assert any("DISCONNECTED" in b.detail for b in report.blockers)

    def test_a_dead_feed_is_only_an_advisory_on_paper(self):
        report = preflight(a_session(), feed_state="STALLED")
        assert report.can_start
        assert any("STALLED" in a.detail for a in report.advisories)

    def test_a_missing_calendar_never_blocks(self):
        # It caps the context score, which is visible everywhere. It is not a
        # reason to refuse to run.
        report = preflight(a_session())
        assert report.can_start
        assert any("calendar" in a.detail for a in report.advisories)


# ---------------------------------------------------------------------------
# The control surface
# ---------------------------------------------------------------------------

class TestEngineControl:
    def test_settings_say_what_may_be_changed_right_now(self):
        session = a_session()
        entries = {e["key"]: e for e in control_for(session).settings()["settings"]}
        assert entries["riskPerTrade"]["editable"] is True
        assert entries["symbol"]["editable"] is False
        assert "history already accumulated" in entries["symbol"]["blockedBecause"]

    def test_an_open_position_locks_the_flat_only_settings(self):
        session = a_session()
        session._position = FakePosition()
        entries = {e["key"]: e for e in control_for(session).settings()["settings"]}
        assert entries["equity"]["editable"] is False
        assert "position is open" in entries["equity"]["blockedBecause"]
        assert entries["riskPerTrade"]["editable"] is True

    def test_applying_records_who_changed_what(self):
        # A configuration change decides what every later order will be. "It
        # must have been running different settings" is not an answer anyone
        # can check six months later.
        control = control_for(a_session())
        control.apply({"riskPerTrade": "0.01"}, who="marcus")
        entry = control.journal[-1]
        assert entry.who == "marcus"
        assert entry.key == "riskPerTrade"
        assert (entry.before, entry.after) == ("0.005", "0.01")

    def test_the_config_hash_moves_with_the_settings(self):
        control = control_for(a_session())
        before = control.settings()["configHash"]
        control.apply({"riskPerTrade": "0.01"}, who="marcus")
        assert control.settings()["configHash"] != before

    def test_going_live_needs_the_phrase_typed(self):
        control = control_for(a_session())
        with pytest.raises(DeterminismError, match="TRADE REAL MONEY"):
            control.apply({"mode": "LIVE"}, who="marcus")
        assert control.read(lambda s: s.config.mode) is Mode.PAPER

    def test_the_wrong_phrase_is_not_close_enough(self):
        control = control_for(a_session())
        with pytest.raises(DeterminismError):
            control.apply({"mode": "LIVE"}, who="m", confirmation="trade real money")

    def test_the_right_phrase_lets_it_through(self):
        control = control_for(a_session())
        control.apply({"mode": "LIVE"}, who="m", confirmation=LIVE_CONFIRMATION)
        assert control.read(lambda s: s.config.mode) is Mode.LIVE

    def test_leaving_live_needs_no_ceremony(self):
        # Every guard here points one way: towards less risk being easy.
        control = control_for(a_session(mode=Mode.LIVE))
        control.apply({"mode": "PAPER"}, who="marcus")
        assert control.read(lambda s: s.config.mode) is Mode.PAPER

    def test_starting_is_refused_when_preflight_blocks(self):
        control = control_for(a_session(mode=Mode.LIVE))
        control.write(lambda s: s.oms.halt("not started"))
        result = control.start(who="marcus")
        assert result["started"] is False
        assert control.read(lambda s: s.oms.is_halted)

    def test_starting_can_be_forced_and_the_override_is_recorded(self):
        # A check can be wrong, and an engine nobody can start is worse than
        # one that warns. "We forced it" is the first question after a bad day.
        control = control_for(a_session(mode=Mode.LIVE))
        control.write(lambda s: s.oms.halt("not started"))
        result = control.start(who="marcus", force=True)
        assert result["started"] is True
        assert "overridden" in result["message"]

    def test_starting_a_clean_session_works(self):
        control = control_for(a_session())
        control.write(lambda s: s.oms.halt("not started"))
        assert control.start(who="marcus")["started"] is True
        assert not control.read(lambda s: s.oms.is_halted)

    def test_stopping_never_closes_a_position(self):
        # The safest button in the app must not be the one that realises a
        # loss at whatever price happens to be showing.
        session = a_session()
        session._position = FakePosition()
        control = control_for(session)
        result = control.stop(who="marcus")
        assert result["halted"] is True
        assert session._position is not None
        assert "still open" in result["message"]

    def test_stopping_and_starting_are_both_journalled(self):
        control = control_for(a_session())
        control.stop(who="marcus")
        control.start(who="marcus")
        assert [c.after for c in control.journal] == ["halted", "running"]

    def test_an_empty_change_set_is_refused(self):
        with pytest.raises(DeterminismError, match="no changes"):
            control_for(a_session()).apply({}, who="marcus")


# ---------------------------------------------------------------------------
# Over the wire
# ---------------------------------------------------------------------------

def routed(role: Role = Role.OWNER):
    store = OperatorStore(hasher=PasswordHasher(iterations=100))
    store.add("marcus", GOOD, role)
    tokens = TokenRegistry()
    login = LoginService(store, tokens)
    session = a_session()
    session.oms.halt("not started yet")
    panel = panel_for(session, login=login, configurable=True, allow_resume=True)
    router = Router(panel, tokens)
    token = router.handle(
        "POST", "/api/login", None, {"username": "marcus", "password": GOOD}
    ).body["token"]
    return router, token, session


class TestTheRoutes:
    def test_signing_in_returns_a_usable_session(self):
        router, token, _ = routed()
        who = router.handle("GET", "/api/whoami", token, {})
        assert who.status == 200
        assert who.body["label"] == "marcus"
        assert who.body["canCommand"] is True
        assert who.body["expiresInSeconds"] > 0

    def test_a_bad_password_is_401_with_one_message(self):
        router, _, _ = routed()
        response = router.handle(
            "POST", "/api/login", None,
            {"username": "marcus", "password": "wrong"},
        )
        assert response.status == 401
        assert response.body["error"] == "username or password is wrong"

    def test_an_operator_may_stop_but_not_start(self):
        # The asymmetry over the wire. Login names it; it does not soften it.
        router, token, _ = routed(Role.OPERATOR)
        assert router.handle("POST", "/api/stop", token, {}).status == 200
        refused = router.handle("POST", "/api/start", token, {})
        assert refused.status == 403
        assert "only an OWNER holds" in refused.body["error"]

    def test_an_operator_may_not_reconfigure(self):
        router, token, session = routed(Role.OPERATOR)
        refused = router.handle(
            "POST", "/api/config", token, {"changes": {"riskPerTrade": "0.05"}}
        )
        assert refused.status == 403
        assert session.config.risk.risk_per_trade == dec("0.005")

    def test_a_viewer_may_read_the_settings(self):
        # Someone who cannot see the settings cannot tell whether what they
        # are watching is what they think they are watching.
        router, token, _ = routed(Role.VIEWER)
        assert router.handle("GET", "/api/config", token, {}).status == 200

    def test_a_viewer_may_not_stop(self):
        router, token, _ = routed(Role.VIEWER)
        assert router.handle("POST", "/api/stop", token, {}).status == 403

    def test_applying_a_change_over_the_wire(self):
        router, token, session = routed()
        response = router.handle(
            "POST", "/api/config", token, {"changes": {"riskPerTrade": "0.01"}}
        )
        assert response.status == 200
        assert response.body["changed"] == ["riskPerTrade"]
        assert session.config.risk.risk_per_trade == dec("0.01")

    def test_a_refused_change_is_400_and_changes_nothing(self):
        router, token, session = routed()
        response = router.handle(
            "POST", "/api/config", token, {"changes": {"symbol": "GBPUSD"}}
        )
        assert response.status == 400
        assert session.config.symbol == "EURUSD"

    def test_changes_must_be_an_object(self):
        router, token, _ = routed()
        response = router.handle(
            "POST", "/api/config", token, {"changes": ["riskPerTrade"]}
        )
        assert response.status == 400

    def test_a_blocked_start_is_409_not_200(self):
        # A 200 with started:false is a result the page would have to read
        # carefully to notice. The status code should carry the refusal.
        router, token, session = routed()
        router.handle(
            "POST", "/api/config", token,
            {"changes": {"mode": "LIVE"}, "confirm": LIVE_CONFIRMATION},
        )
        assert router.handle("POST", "/api/start", token, {}).status == 409

    def test_starting_over_the_wire(self):
        router, token, session = routed()
        assert session.oms.is_halted
        response = router.handle("POST", "/api/start", token, {})
        assert response.status == 200
        assert not session.oms.is_halted

    def test_logging_out_ends_the_session(self):
        router, token, _ = routed()
        assert router.handle("POST", "/api/logout", token, {}).status == 200
        assert router.handle("GET", "/api/status", token, {}).status == 401

    def test_a_panel_without_a_control_answers_501_not_403(self):
        # "This engine does not do that" is a different fact from "you may
        # not", and conflating them sends people hunting for a permission they
        # were never denied.
        from elyon.modules.api.domain import command_token

        session = a_session()
        tokens = TokenRegistry()
        console = command_token()
        tokens.add(console)
        router = Router(panel_for(session), tokens)
        response = router.handle("POST", "/api/start", console.secret, {})
        assert response.status == 501
        assert "console actions" in response.body["error"]

    def test_login_is_501_when_there_are_no_accounts(self):
        from elyon.modules.api.domain import phone_token

        tokens = TokenRegistry()
        tokens.add(phone_token())
        router = Router(panel_for(a_session()), tokens)
        response = router.handle(
            "POST", "/api/login", None, {"username": "x", "password": "y"}
        )
        assert response.status == 501

    def test_an_oversized_body_is_not_read(self):
        # An unbounded read on an unauthenticated route is a way to exhaust
        # the memory of a machine that is holding a position.
        from elyon.modules.api.domain import MAX_BODY_BYTES

        assert MAX_BODY_BYTES <= 1024 * 1024
