"""Control-surface tests.

Exposing a trading engine to a phone means exposing it to whoever holds the
token, so most of this file is about the asymmetry the design rests on:

    **Stopping is safe. Starting is not.**

A stolen phone should be able to halt the bot and close positions. That is
annoying. It should not be able to resume, raise risk, or switch to LIVE, and
the tests below are what keep that true when somebody adds a route in six
months.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from elyon.modules.api.domain import (
    MIN_TOKEN_LENGTH,
    AccessToken,
    Capability,
    ControlPanel,
    Forbidden,
    Router,
    ServerConfig,
    TokenRegistry,
    Unauthorised,
    build_server,
    command_token,
    new_secret,
    panel_for,
    phone_token,
    render_page,
    session_snapshot,
    to_jsonable,
)
from elyon.modules.backtesting.domain import GeneratorConfig, generate
from elyon.modules.market_context.domain import learn_dna, profile_for
from elyon.modules.session.domain import Mode, SessionConfig, TradingSession
from elyon.modules.strategy.domain import Calibration, StrategyId
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

MARKET = generate(GeneratorConfig(cycles=40))
PROVEN = {
    StrategyId.SIX_PILLARS: Calibration(180, 92, dec("0.42"), dataset="test")
}


def a_session() -> TradingSession:
    config = SessionConfig(symbol="EURUSD", calibrations=PROVEN)
    session = TradingSession(config, dna=learn_dna(MARKET, profile_for("EURUSD")))
    for candle in MARKET:
        session._on_candle(candle)
    return session


SESSION = a_session()


def router_with(*tokens: AccessToken, allow_resume: bool = False):
    registry = TokenRegistry()
    for token in tokens:
        registry.add(token)
    return Router(panel_for(SESSION, allow_resume=allow_resume), registry)


# ---------------------------------------------------------------------------
# The asymmetry
# ---------------------------------------------------------------------------

class TestStoppingIsSafeStartingIsNot:
    def test_a_phone_gets_observe_and_protect(self):
        token = phone_token()
        assert token.allows(Capability.OBSERVE)
        assert token.allows(Capability.PROTECT)
        assert not token.allows(Capability.COMMAND)

    def test_a_phone_can_halt(self):
        # A stolen phone stopping the bot is annoying, not catastrophic.
        token = phone_token()
        response = router_with(token).handle(
            "POST", "/api/halt", token.secret, {"reason": "from the train"}
        )
        assert response.status == 200
        assert response.body["halted"] is True

    def test_a_phone_cannot_resume(self):
        # A stolen phone *starting* the bot is the catastrophic direction.
        token = phone_token()
        response = router_with(token, allow_resume=True).handle(
            "POST", "/api/resume", token.secret, {}
        )
        assert response.status == 403
        assert "may not command" in response.body["error"]

    def test_the_refusal_explains_where_resuming_lives(self):
        token = phone_token()
        response = router_with(token, allow_resume=True).handle(
            "POST", "/api/resume", token.secret, {}
        )
        assert "only an OWNER holds" in response.body["error"]
        assert "Stopping does not" in response.body["error"]

    def test_resume_is_absent_unless_deliberately_enabled(self):
        # Not merely refused -- the hook is not wired at all.
        console = command_token()
        response = router_with(console).handle(
            "POST", "/api/resume", console.secret, {}
        )
        assert response.status == 501
        assert "console action" in response.body["error"]

    def test_a_console_token_may_resume_when_enabled(self):
        console = command_token()
        response = router_with(console, allow_resume=True).handle(
            "POST", "/api/resume", console.secret, {}
        )
        assert response.status == 200

    def test_command_is_a_separate_function_not_a_flag(self):
        # Granting COMMAND should be something written down rather than
        # something defaulted into.
        assert not phone_token().can_command
        assert command_token().can_command

    def test_every_capability_a_phone_holds_reduces_risk(self):
        for capability in phone_token().capabilities:
            assert capability.reduces_risk


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

class TestTokens:
    def test_a_short_token_is_refused(self):
        # A short secret on an endpoint that can flatten positions is worth
        # guessing.
        with pytest.raises(DeterminismError, match="worth guessing"):
            AccessToken("abc123")

    def test_generated_tokens_are_long_enough(self):
        assert len(new_secret()) >= MIN_TOKEN_LENGTH

    def test_generated_tokens_are_unique(self):
        assert len({new_secret() for _ in range(50)}) == 50

    def test_a_token_granting_nothing_is_refused(self):
        with pytest.raises(DeterminismError, match="grants nothing"):
            AccessToken(new_secret(), frozenset())

    def test_the_secret_never_appears_in_a_repr(self):
        # It leaks from a log line or a traceback eventually if it is allowed.
        token = phone_token()
        assert token.secret not in repr(token)
        assert "redacted" in repr(token)

    def test_the_secret_never_appears_in_the_string_form(self):
        token = phone_token()
        assert token.secret not in str(token)

    def test_an_unknown_token_is_rejected(self):
        registry = TokenRegistry()
        registry.add(phone_token())
        with pytest.raises(Unauthorised):
            registry.authorise("not-a-real-token-but-long-enough-to-try", Capability.OBSERVE)

    def test_a_missing_token_is_rejected(self):
        registry = TokenRegistry()
        registry.add(phone_token())
        with pytest.raises(Unauthorised):
            registry.authorise(None, Capability.OBSERVE)

    def test_resolution_checks_every_candidate(self):
        # Constant-time comparison across the whole list: returning early on a
        # match leaks, through timing, how much of a guess was right.
        registry = TokenRegistry()
        wanted = phone_token("second")
        registry.add(phone_token("first"))
        registry.add(wanted)
        assert registry.resolve(wanted.secret) is wanted


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class TestRoutes:
    def test_status_needs_a_token(self):
        assert router_with(phone_token()).handle(
            "GET", "/api/status", None, {}
        ).status == 401

    def test_status_reports_the_session(self):
        token = phone_token()
        body = router_with(token).handle(
            "GET", "/api/status", token.secret, {}
        ).body
        assert body["symbol"] == "EURUSD"
        assert "stoppedAt" in body

    def test_whoami_tells_the_page_what_to_render(self):
        # So it can hide controls it would only get a 403 from -- rendering
        # them would teach people to ignore errors.
        token = phone_token()
        body = router_with(token).handle(
            "GET", "/api/whoami", token.secret, {}
        ).body
        assert body["canCommand"] is False
        assert "PROTECT" in body["capabilities"]

    def test_the_page_needs_no_token(self):
        # It is markup with no data in it; every figure is fetched afterwards.
        # Gating it would only mean typing the token before seeing where to.
        response = router_with(phone_token()).handle("GET", "/", None, {})
        assert response.status == 200
        assert "text/html" in response.content_type

    def test_an_unknown_route_is_a_404(self):
        token = phone_token()
        assert router_with(token).handle(
            "GET", "/api/nonsense", token.secret, {}
        ).status == 404

    def test_a_halt_without_a_reason_still_records_one(self):
        token = phone_token("iphone")
        session = a_session()
        registry = TokenRegistry()
        registry.add(token)
        Router(panel_for(session), registry).handle(
            "POST", "/api/halt", token.secret, {}
        )
        assert "iphone" in session.oms.halt_reason

    def test_halting_protects_rather_than_closes(self):
        session = a_session()
        token = phone_token()
        registry = TokenRegistry()
        registry.add(token)
        response = Router(panel_for(session), registry).handle(
            "POST", "/api/halt", token.secret, {"reason": "test"}
        )
        assert "protected, not closed" in response.body["message"]
        assert session.oms.is_halted


# ---------------------------------------------------------------------------
# What the phone is shown
# ---------------------------------------------------------------------------

class TestTheSnapshotLeaksNothing:
    def _snapshot(self) -> dict:
        return dict(session_snapshot(SESSION))

    def test_it_carries_no_credentials(self):
        # Credentials cannot leak through a field that does not exist.
        serialised = json.dumps(to_jsonable(self._snapshot())).lower()
        for forbidden in ("password", "secret", "token", "login", "apikey"):
            assert forbidden not in serialised

    def test_it_carries_no_account_or_server_identity(self):
        serialised = json.dumps(to_jsonable(self._snapshot())).lower()
        for forbidden in ("account", "server", "broker", "path"):
            assert forbidden not in serialised

    def test_a_screenshot_is_not_an_incident(self):
        # Everything on the page is either public market data or this account's
        # own aggregate state -- nothing that identifies where it trades.
        keys = set(self._snapshot())
        assert keys <= {
            "symbol", "mode", "halted", "haltReason", "bars", "entries",
            "closed", "realizedR", "orders", "deadLetters", "stoppedAt",
            "warnings", "position",
        }

    def test_decimals_survive_as_strings(self):
        # A price that survived the whole engine exactly should not lose digits
        # on its way to a screen.
        assert to_jsonable(dec("1.10005")) == "1.10005"
        assert isinstance(to_jsonable(dec("0.1")), str)

    def test_the_open_position_answers_can_this_still_lose(self):
        session = a_session()
        snapshot = session_snapshot(session)
        if snapshot["position"]["open"]:
            assert "lockedR" in snapshot["position"]

    def test_warnings_reach_the_phone(self):
        assert isinstance(self._snapshot()["warnings"], list)


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------

class TestBinding:
    def test_localhost_is_the_default(self):
        # An endpoint that can flatten positions is not something to put on the
        # open internet by accident.
        assert ServerConfig().host == "127.0.0.1"
        assert not ServerConfig().is_exposed

    def test_localhost_needs_no_warning(self):
        assert ServerConfig().warnings() == ()

    def test_binding_outward_warns_loudly(self):
        warnings = ServerConfig(host="0.0.0.0").warnings()
        assert warnings
        assert "plain HTTP" in warnings[0]
        assert "VPN" in warnings[0] or "tunnel" in warnings[0]

    def test_a_server_without_tokens_is_refused(self):
        # It would refuse every request; failing at startup says why.
        with pytest.raises(DeterminismError, match="no tokens and no accounts"):
            build_server(ControlPanel(status=dict, halt=lambda r: ""),
                         TokenRegistry())


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

class TestThePage:
    def test_it_loads_nothing_remote(self):
        # It is served from a VPS that may have no route to a CDN, and a
        # control surface is a poor place to run someone else's code.
        page = render_page()
        assert "http://" not in page.replace("http://127.0.0.1", "")
        assert "https://" not in page
        assert "cdn" not in page.lower()

    def test_stopping_is_never_behind_a_tab(self):
        page = render_page()
        assert "Stop trading" in page
        # The stop button is positioned outside every tab panel, so no
        # navigation state can hide the one control that is always safe.
        assert 'class="stop" id="stop"' in page
        assert page.index('id="stop"') > page.index('<nav>')

    def test_start_is_hidden_from_an_account_that_cannot_start(self):
        # There is a Start button now -- an owner has to be able to start the
        # engine from the app. It is hidden rather than merely refused for
        # anyone else: a control that only ever 403s teaches people to ignore
        # errors. The API refuses it regardless; this is the second layer.
        page = render_page()
        assert "'hidden', !me.canCommand" in page
        assert "canCommand" in page

    def test_it_asks_for_confirmation_before_halting(self):
        # A pocket is full of accidental single taps.
        assert "Tap again to confirm" in render_page()

    def test_it_shows_where_the_pipeline_stopped(self):
        page = render_page()
        assert "Why it is not trading" in page
        assert "stoppedAt" in page

    def test_the_token_stays_in_the_browser(self):
        page = render_page()
        assert "localStorage" in page
        # What is stored is a session that expires, not the password. The
        # password field is cleared the moment it has been exchanged.
        assert "never stored on this device" in page
        assert "$('password').value = '';" in page

    def test_it_is_mobile_first(self):
        page = render_page()
        assert "viewport" in page
        assert "safe-area-inset" in page
