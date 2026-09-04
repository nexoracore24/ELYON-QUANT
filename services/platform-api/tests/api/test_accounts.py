"""Signing in.

The tests that matter here are not "a correct password works". They are the
ones about what happens when it does not: how long a wrong answer takes, what a
wrong answer says, and whether repeating it is cheap.
"""

from __future__ import annotations

import json
import os
import stat
import time

import pytest

from elyon.modules.api.domain import (
    LoginService,
    LoginThrottle,
    Operator,
    OperatorStore,
    PasswordHasher,
    Role,
    TokenRegistry,
    TooManyAttempts,
    Unauthorised,
    check_password_strength,
    normalise_username,
)
from elyon.modules.api.domain.accounts import WRONG_CREDENTIALS
from elyon.modules.api.domain.auth import Capability
from elyon.shared_kernel.edcs.numeric import DeterminismError

# Real iteration counts make a test suite take minutes. The cost is a property
# of the deployed hasher, not of the logic being checked here.
FAST = PasswordHasher(iterations=100)
GOOD = "correct-horse-battery"


def store_with(*people: tuple[str, str, Role]) -> OperatorStore:
    store = OperatorStore(hasher=FAST)
    for username, password, role in people:
        store.add(username, password, role)
    return store


def service(store: OperatorStore, **kwargs) -> LoginService:
    return LoginService(store, TokenRegistry(), **kwargs)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

class TestHashing:
    def test_the_same_password_hashes_differently_every_time(self):
        # A shared salt would let one rainbow table cover every account, and
        # would say which two operators picked the same password.
        first, second = FAST.hash(GOOD), FAST.hash(GOOD)
        assert first != second
        assert FAST.verify(GOOD, first)
        assert FAST.verify(GOOD, second)

    def test_the_wrong_password_does_not_verify(self):
        assert not FAST.verify("something else", FAST.hash(GOOD))

    def test_the_iteration_count_travels_with_the_hash(self):
        # So the cost can be raised later without invalidating every existing
        # password on the next login.
        cheap = PasswordHasher(iterations=100).hash(GOOD)
        assert PasswordHasher(iterations=999_999).verify(GOOD, cheap)
        assert cheap.split("$")[1] == "100"

    def test_a_corrupt_hash_is_a_failed_login_not_a_crash(self):
        # One mangled line in the operator file should lock out one account,
        # not take down the control surface of a system holding a position.
        for broken in ("", "garbage", "pbkdf2_sha256$notanumber$x$y",
                       "argon2$1$x$y", "a$b$c"):
            assert FAST.verify(GOOD, broken) is False


class TestPasswordStrength:
    def test_short_passwords_are_refused(self):
        with pytest.raises(DeterminismError, match="at least 12"):
            check_password_strength("short1!")

    def test_the_username_cannot_be_the_password(self):
        with pytest.raises(DeterminismError, match="contains the username"):
            check_password_strength("marcusmarcus123", "marcus")

    def test_the_obvious_ones_are_refused(self):
        with pytest.raises(DeterminismError, match="guessing list"):
            check_password_strength("changeme1234")

    def test_repetition_is_refused(self):
        with pytest.raises(DeterminismError, match="distinct characters"):
            check_password_strength("aaaaaaaaaaaaaaa")

    def test_a_reasonable_password_is_accepted(self):
        check_password_strength(GOOD, "marcus")


class TestUsernames:
    @pytest.mark.parametrize("raw,expected", [
        ("Marcus", "marcus"), ("  owner  ", "owner"), ("a.b-c_d", "a.b-c_d"),
    ])
    def test_normalised(self, raw, expected):
        assert normalise_username(raw) == expected

    @pytest.mark.parametrize("bad", ["ab", "", "has space", "..dots", "x" * 40])
    def test_refused(self, bad):
        with pytest.raises(DeterminismError):
            normalise_username(bad)


# ---------------------------------------------------------------------------
# The operator file
# ---------------------------------------------------------------------------

class TestTheOperatorFile:
    def test_it_round_trips(self, tmp_path):
        path = tmp_path / "operators.json"
        store = OperatorStore.load(path, hasher=FAST)
        store.add("owner", GOOD, Role.OWNER)
        reloaded = OperatorStore.load(path, hasher=FAST)
        assert set(reloaded.operators) == {"owner"}
        assert reloaded.operators["owner"].role is Role.OWNER

    def test_it_is_written_private(self, tmp_path):
        # It holds password hashes. World-readable is an offline guessing run
        # waiting for anyone with a shell on the box.
        path = tmp_path / "operators.json"
        store = OperatorStore.load(path, hasher=FAST)
        store.add("owner", GOOD, Role.OWNER)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_the_password_is_not_in_the_file(self, tmp_path):
        path = tmp_path / "operators.json"
        OperatorStore.load(path, hasher=FAST).add("owner", GOOD, Role.OWNER)
        assert GOOD not in path.read_text()

    def test_no_temporary_file_is_left_behind(self, tmp_path):
        path = tmp_path / "operators.json"
        OperatorStore.load(path, hasher=FAST).add("owner", GOOD, Role.OWNER)
        assert [p.name for p in tmp_path.iterdir()] == ["operators.json"]

    def test_a_missing_file_is_an_empty_store_not_an_error(self, tmp_path):
        # First run. Failing here would mean `elyon useradd` could never
        # create the first account.
        store = OperatorStore.load(tmp_path / "nothing.json")
        assert len(store) == 0

    def test_duplicate_usernames_are_refused(self):
        store = store_with(("owner", GOOD, Role.OWNER))
        with pytest.raises(DeterminismError, match="already exists"):
            store.add("owner", "another-good-password", Role.VIEWER)

    def test_the_last_owner_cannot_be_removed(self):
        # It would leave an engine nobody can start, reconfigure or take out
        # of LIVE mode -- recoverable only by editing a file on the host.
        store = store_with(("owner", GOOD, Role.OWNER),
                           ("watcher", GOOD + "x", Role.OPERATOR))
        with pytest.raises(DeterminismError, match="only owner"):
            store.remove("owner")
        store.remove("watcher")

    def test_the_last_owner_cannot_be_demoted(self):
        store = store_with(("owner", GOOD, Role.OWNER))
        with pytest.raises(DeterminismError, match="only owner"):
            store.set_role("owner", Role.VIEWER)

    def test_a_second_owner_makes_the_first_removable(self):
        store = store_with(("alice", GOOD, Role.OWNER), ("bob", GOOD + "x", Role.OWNER))
        store.remove("alice")
        assert set(store.operators) == {"bob"}

    def test_the_hash_is_not_in_a_repr(self):
        # Where credentials leak from: a traceback, a log line, a debugger.
        operator = store_with(("owner", GOOD, Role.OWNER)).require("owner")
        assert "pbkdf2" not in repr(operator)
        assert "owner" in repr(operator)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

class TestRoles:
    def test_stopping_is_available_one_level_below_starting(self):
        # The whole asymmetry, in one assertion. Login gives it a name; it does
        # not soften it.
        assert Capability.PROTECT in Role.OPERATOR.capabilities
        assert Capability.COMMAND not in Role.OPERATOR.capabilities
        assert Capability.COMMAND in Role.OWNER.capabilities

    def test_a_viewer_can_only_look(self):
        assert Role.VIEWER.capabilities == frozenset({Capability.OBSERVE})

    def test_every_role_grants_something(self):
        # A role granting nothing would only ever produce 403s.
        for role in Role:
            assert role.capabilities


# ---------------------------------------------------------------------------
# Logging in
# ---------------------------------------------------------------------------

class TestLogin:
    def test_a_correct_password_produces_a_session(self):
        login = service(store_with(("owner", GOOD, Role.OWNER)))
        token = login.login("owner", GOOD)
        assert token.can_command
        assert token.label == "owner"
        assert login.tokens.resolve(token.secret) is token

    def test_the_username_is_case_insensitive(self):
        login = service(store_with(("owner", GOOD, Role.OWNER)))
        assert login.login("OWNER", GOOD).label == "owner"

    def test_a_wrong_password_is_refused(self):
        login = service(store_with(("owner", GOOD, Role.OWNER)))
        with pytest.raises(Unauthorised, match=WRONG_CREDENTIALS):
            login.login("owner", "not it")

    def test_an_unknown_user_gets_the_same_message(self):
        # Otherwise the form is an account enumerator, and the first thing an
        # attacker learns is which name to spend their guesses on.
        login = service(store_with(("owner", GOOD, Role.OWNER)))
        with pytest.raises(Unauthorised) as unknown:
            login.login("nobody", GOOD)
        with pytest.raises(Unauthorised) as wrong:
            login.login("owner", "not it")
        assert str(unknown.value) == str(wrong.value) == WRONG_CREDENTIALS

    def test_an_unknown_user_costs_the_same_work(self):
        # And the same message is worthless if the timing gives it away. A
        # deliberately expensive hasher makes the difference measurable.
        slow = PasswordHasher(iterations=60_000)
        store = OperatorStore(hasher=slow)
        store.add("owner", GOOD, Role.OWNER)
        login = LoginService(store, TokenRegistry(),
                             throttle=LoginThrottle(per_client_attempts=999))

        def timed(username: str) -> float:
            start = time.perf_counter()
            with pytest.raises(Unauthorised):
                login.login(username, "wrong password here")
            return time.perf_counter() - start

        known = min(timed("owner") for _ in range(3))
        unknown = min(timed("ghost") for _ in range(3))
        # Not equality -- this is wall-clock time on a shared machine. The
        # claim is that the unknown branch does not return early.
        assert unknown > known / 2

    def test_a_disabled_account_cannot_sign_in(self):
        store = store_with(("owner", GOOD, Role.OWNER))
        store.operators["owner"] = Operator(
            username="owner", password_hash=store.require("owner").password_hash,
            role=Role.OWNER, disabled=True,
        )
        with pytest.raises(Unauthorised):
            service(store).login("owner", GOOD)

    def test_the_password_is_not_reusable_as_a_token(self):
        # The point of the exchange: what travels afterwards is not the secret
        # a person knows.
        login = service(store_with(("owner", GOOD, Role.OWNER)))
        login.login("owner", GOOD)
        assert login.tokens.resolve(GOOD) is None

    def test_logging_out_ends_that_session_only(self):
        login = service(store_with(("owner", GOOD, Role.OWNER)))
        phone = login.login("owner", GOOD)
        laptop = login.login("owner", GOOD)
        assert login.logout(phone.secret) is True
        assert login.tokens.resolve(phone.secret) is None
        assert login.tokens.resolve(laptop.secret) is laptop

    def test_logging_out_twice_is_not_an_error(self):
        login = service(store_with(("owner", GOOD, Role.OWNER)))
        token = login.login("owner", GOOD)
        assert login.logout(token.secret) is True
        assert login.logout(token.secret) is False

    def test_every_session_can_be_revoked_at_once(self):
        # What a person actually wants after losing a phone, and the reason a
        # session is worth having: a password cannot be recalled, a session can.
        login = service(store_with(("owner", GOOD, Role.OWNER)))
        tokens = [login.login("owner", GOOD) for _ in range(3)]
        assert login.tokens.revoke_all("owner") == 3
        assert all(login.tokens.resolve(t.secret) is None for t in tokens)


class TestThrottling:
    def test_repeated_failures_lock_the_client_out(self):
        login = service(store_with(("owner", GOOD, Role.OWNER)))
        for _ in range(5):
            with pytest.raises(Unauthorised):
                login.login("owner", "wrong", client="1.2.3.4")
        with pytest.raises(TooManyAttempts, match="try again in"):
            login.login("owner", GOOD, client="1.2.3.4")

    def test_another_client_is_unaffected(self):
        # The lockout must not be a denial-of-service anyone can aim at the
        # owner: five wrong guesses should not lock them out of their own
        # engine while a position is open.
        login = service(store_with(("owner", GOOD, Role.OWNER)))
        for _ in range(20):
            with pytest.raises(Unauthorised):
                login.login("owner", "wrong", client="10.0.0.1")
        assert login.login("owner", GOOD, client="192.168.1.5").can_command

    def test_a_correct_password_clears_the_count(self):
        login = service(store_with(("owner", GOOD, Role.OWNER)))
        for _ in range(3):
            with pytest.raises(Unauthorised):
                login.login("owner", "wrong", client="1.2.3.4")
        login.login("owner", GOOD, client="1.2.3.4")
        for _ in range(3):
            with pytest.raises(Unauthorised):
                login.login("owner", "wrong", client="1.2.3.4")
        # Still under the limit, because the counter restarted.
        with pytest.raises(Unauthorised, match=WRONG_CREDENTIALS):
            login.login("owner", "wrong", client="1.2.3.4")

    def test_the_lockout_lengthens(self):
        throttle = LoginThrottle(per_client_attempts=2, base_lockout_seconds=10)
        for _ in range(5):
            throttle.record_failure("ip:x", now=0.0)
        # The limit trips at 10s, and every failure past it doubles the wait:
        # 10, 20, 40, 80 for the 2nd through 5th.
        assert throttle.locked_out("ip:x", now=79.0)
        assert not throttle.locked_out("ip:x", now=81.0)

    def test_the_lockout_is_capped(self):
        throttle = LoginThrottle(
            per_client_attempts=1, base_lockout_seconds=10, max_lockout_seconds=60
        )
        for _ in range(30):
            throttle.record_failure("ip:x", now=0.0)
        assert not throttle.locked_out("ip:x", now=61.0)


class TestSessionExpiry:
    def test_a_session_expires(self):
        login = service(store_with(("owner", GOOD, Role.OWNER)),
                        session_ttl_seconds=0.05)
        token = login.login("owner", GOOD)
        assert login.tokens.resolve(token.secret) is token
        time.sleep(0.06)
        assert login.tokens.resolve(token.secret) is None

    def test_an_expired_session_says_so(self):
        # "Sign in again" is a far better answer than "unknown token" for the
        # one person who will see it most, and it leaks nothing: they already
        # held a credential that was valid.
        login = service(store_with(("owner", GOOD, Role.OWNER)),
                        session_ttl_seconds=0.05)
        token = login.login("owner", GOOD)
        time.sleep(0.06)
        with pytest.raises(Unauthorised, match="sign in again"):
            login.tokens.authorise(token.secret, Capability.OBSERVE)

    def test_silence_ends_a_session_early(self):
        tokens = TokenRegistry(idle_timeout_seconds=0.05)
        login = LoginService(store_with(("owner", GOOD, Role.OWNER)), tokens)
        token = login.login("owner", GOOD)
        time.sleep(0.03)
        assert tokens.resolve(token.secret) is token   # use it, and it survives
        time.sleep(0.03)
        assert tokens.resolve(token.secret) is token   # still in use
        time.sleep(0.06)
        assert tokens.resolve(token.secret) is None    # left alone, it lapses

    def test_a_printed_token_does_not_expire(self):
        # Someone typed a command to create it. That is a different decision
        # from a session opened on a phone.
        from elyon.modules.api.domain import phone_token

        registry = TokenRegistry()
        token = phone_token("console")
        registry.add(token)
        assert token.expires_at is None
        assert token.seconds_remaining() is None
        assert registry.resolve(token.secret) is token
