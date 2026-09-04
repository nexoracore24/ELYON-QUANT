"""Operators, passwords, and turning one into a session.

Until now the engine authenticated with a token it printed at startup. That is
right for a thing you watch and stop, and wrong for a thing you *configure and
start*: nobody keeps a 43-character string in their head, and a credential
people paste from a notes app is a credential that lives in a notes app.

So: a login. Four decisions carry it, and the first is the one everything else
depends on.

**A password is not an API credential.** Logging in exchanges the password for a
short-lived token, and the token is what every subsequent request carries. The
password crosses the wire once per session instead of every five seconds, and
what ends up in the browser's storage is something that expires on its own.

**Failed logins cost time, and the cost compounds.** A login form on a trading
engine that answers instantly is a form worth guessing at. Attempts are
throttled and lockouts double. Locking purely on *username* would be worse than
useless -- anyone who knows your name could lock you out of your own engine at
the exact moment you needed to stop it -- so the strict counter is per client
address, and the per-account counter is deliberately generous.

**A wrong username and a wrong password are the same answer, and take the same
time.** Otherwise the form is an account enumerator, and the first thing an
attacker learns is which name to spend their guesses on.

**The first account is created off the network.** ``elyon useradd``, on the
machine. There is no default login: a default credential on a system that can
place orders is not a convenience, it is a donation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from elyon.shared_kernel.edcs.numeric import DeterminismError

from .auth import AccessToken, Capability, TokenRegistry, Unauthorised

# OWASP's floor for PBKDF2-HMAC-SHA256 at the time of writing. Deliberately
# expensive: the whole defence of a stolen password file is that verifying one
# guess is slow.
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 12
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")

# Passwords that are guessed first, every time. Not a serious blocklist -- a
# serious one is a file nobody maintains -- just the handful that show up in the
# first hundred attempts of any credential-stuffing run.
_OBVIOUS = frozenset({
    "password", "passw0rd", "password123", "123456789012", "qwertyuiop123",
    "letmein12345", "elyonquant", "elyon-quant", "administrator",
    "trading12345", "changeme1234",
})


class Role(str, Enum):
    """A name for a set of capabilities, not a new idea.

    The asymmetry the token model is built on survives login unchanged:
    stopping is safe, starting is not. An OPERATOR can halt the engine at 3am
    from a phone. Only an OWNER can start it again, raise the risk, or switch to
    LIVE.
    """

    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    OWNER = "OWNER"

    @property
    def capabilities(self) -> frozenset[Capability]:
        return {
            Role.VIEWER: frozenset({Capability.OBSERVE}),
            Role.OPERATOR: frozenset({Capability.OBSERVE, Capability.PROTECT}),
            Role.OWNER: frozenset({
                Capability.OBSERVE, Capability.PROTECT, Capability.COMMAND,
            }),
        }[self]

    @property
    def summary(self) -> str:
        return {
            Role.VIEWER: "watch only",
            Role.OPERATOR: "watch and stop",
            Role.OWNER: "watch, stop, configure and start",
        }[self]


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PasswordHasher:
    """PBKDF2-HMAC-SHA256, from the standard library.

    No argon2, no bcrypt, no dependency. The engine's monitoring must not stop
    working because a package index is down, and that applies to the thing that
    lets you in to use it.

    The iteration count is stored *in* the hash rather than read from here, so
    raising the cost later does not invalidate every existing password.
    """

    iterations: int = PBKDF2_ITERATIONS

    def hash(self, password: str, *, salt: bytes | None = None) -> str:
        salt = salt or secrets.token_bytes(SALT_BYTES)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, self.iterations
        )
        return "$".join((
            "pbkdf2_sha256",
            str(self.iterations),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ))

    def verify(self, password: str, encoded: str) -> bool:
        try:
            algorithm, iterations, salt_b64, digest_b64 = encoded.split("$")
            if algorithm != "pbkdf2_sha256":
                return False
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(digest_b64)
            rounds = int(iterations)
        except (ValueError, TypeError):
            # A malformed hash is not a password that matches. It is also not a
            # crash: a corrupted line in the operator file should lock one
            # account out, not take the control surface down with it.
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, rounds
        )
        return hmac.compare_digest(actual, expected)

    def spend_the_same_time(self, password: str) -> None:
        """Verify against nothing, for an account that does not exist.

        Returning immediately when the username is unknown makes the response
        time an oracle: fast means "no such user", slow means "keep guessing
        that one". This burns the same work and throws it away.
        """
        self.verify(password, self.hash(password, salt=b"\x00" * SALT_BYTES))


def check_password_strength(password: str, username: str = "") -> None:
    """Refuse the passwords that make the rest of this pointless."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise DeterminismError(
            f"password is {len(password)} characters; at least "
            f"{MIN_PASSWORD_LENGTH} are required. This credential can place "
            f"orders."
        )
    lowered = password.lower()
    if lowered in _OBVIOUS:
        raise DeterminismError(
            "that password is in every guessing list ever assembled"
        )
    if username and username.lower() in lowered:
        raise DeterminismError(
            "the password contains the username, which is the first thing "
            "anyone tries"
        )
    if len(set(password)) < 5:
        raise DeterminismError(
            "the password uses too few distinct characters to be worth the "
            "hashing"
        )


def normalise_username(raw: str) -> str:
    username = str(raw).strip().lower()
    if not USERNAME_PATTERN.match(username):
        raise DeterminismError(
            f"{raw!r} is not a usable username: 3-32 characters, starting with "
            f"a letter or digit, then letters, digits, dot, dash or underscore"
        )
    return username


# ---------------------------------------------------------------------------
# The operator file
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Operator:
    """One person who may sign in."""

    username: str
    password_hash: str
    role: Role = Role.OPERATOR
    created_at: str = ""
    disabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "passwordHash": self.password_hash,
            "role": self.role.value,
            "createdAt": self.created_at,
            "disabled": self.disabled,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Operator":
        return cls(
            username=str(raw["username"]),
            password_hash=str(raw["passwordHash"]),
            role=Role(raw.get("role", "OPERATOR")),
            created_at=str(raw.get("createdAt", "")),
            disabled=bool(raw.get("disabled", False)),
        )

    def __repr__(self) -> str:
        # The hash never appears in a traceback. It is not a password, but it
        # is the thing an offline guessing run needs.
        return f"Operator(username={self.username!r}, role={self.role.value})"


@dataclass(slots=True)
class OperatorStore:
    """Who may sign in, on disk.

    A plain JSON file, written ``0600``, holding password *hashes*. It is not a
    user database and does not want to be one -- a single-tenant engine has a
    handful of operators and no need for a schema migration.
    """

    operators: dict[str, Operator] = field(default_factory=dict)
    path: Path | None = None
    hasher: PasswordHasher = field(default_factory=PasswordHasher)

    # -- persistence ------------------------------------------------------

    @classmethod
    def load(
        cls, path: str | Path, *, hasher: PasswordHasher | None = None
    ) -> "OperatorStore":
        location = Path(path)
        store = cls(path=location, hasher=hasher or PasswordHasher())
        if not location.exists():
            return store
        raw = json.loads(location.read_text() or "{}")
        for entry in raw.get("operators", []):
            operator = Operator.from_dict(entry)
            store.operators[operator.username] = operator
        return store

    def save(self) -> None:
        if self.path is None:
            return
        payload = json.dumps(
            {"operators": [o.to_dict() for o in self.sorted()]}, indent=2
        )
        # Written to a temporary file in the same directory and renamed, so an
        # interrupted write cannot leave a half-file where the accounts were.
        # Permissions are set before the content goes in, not after.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        with os.fdopen(descriptor, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    # -- membership -------------------------------------------------------

    def add(
        self, username: str, password: str, role: Role = Role.OPERATOR
    ) -> Operator:
        name = normalise_username(username)
        if name in self.operators:
            raise DeterminismError(f"{name!r} already exists")
        check_password_strength(password, name)
        operator = Operator(
            username=name,
            password_hash=self.hasher.hash(password),
            role=role,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.operators[name] = operator
        self.save()
        return operator

    def set_password(self, username: str, password: str) -> None:
        name = normalise_username(username)
        operator = self.require(name)
        check_password_strength(password, name)
        self.operators[name] = replace(
            operator, password_hash=self.hasher.hash(password)
        )
        self.save()

    def set_role(self, username: str, role: Role) -> None:
        name = normalise_username(username)
        operator = self.require(name)
        if (
            operator.role is Role.OWNER
            and role is not Role.OWNER
            and self._owner_count() == 1
        ):
            raise DeterminismError(
                "this is the only owner; demoting it would leave an engine "
                "nobody can start or reconfigure"
            )
        self.operators[name] = replace(operator, role=role)
        self.save()

    def remove(self, username: str) -> None:
        name = normalise_username(username)
        operator = self.require(name)
        if operator.role is Role.OWNER and self._owner_count() == 1:
            raise DeterminismError(
                "this is the only owner; removing it would leave an engine "
                "nobody can start or reconfigure"
            )
        del self.operators[name]
        self.save()

    def require(self, username: str) -> Operator:
        operator = self.operators.get(username)
        if operator is None:
            raise DeterminismError(f"no operator named {username!r}")
        return operator

    def get(self, username: str) -> Operator | None:
        return self.operators.get(username)

    def sorted(self) -> list[Operator]:
        return [self.operators[k] for k in sorted(self.operators)]

    def _owner_count(self) -> int:
        return sum(
            1 for o in self.operators.values()
            if o.role is Role.OWNER and not o.disabled
        )

    def __len__(self) -> int:
        return len(self.operators)

    def summary(self) -> str:
        if not self.operators:
            return "  (no operators; nobody can sign in)"
        return "\n".join(
            f"  {o.username:<20} {o.role.value:<9} {o.role.summary}"
            + ("  [disabled]" if o.disabled else "")
            for o in self.sorted()
        )


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------

class TooManyAttempts(Unauthorised):
    """Locked out, for now."""


@dataclass(slots=True)
class LoginThrottle:
    """Makes guessing expensive without handing anyone a lock-out button.

    Two counters, for two different threats:

    *   **Per client address, strict.** This is the one that stops guessing. An
        attacker controls their own address, so slowing it down costs them and
        nobody else.
    *   **Per account, generous.** A backstop for a distributed attempt. It is
        deliberately hard to trip, because a tight per-account lockout is a
        denial-of-service anyone can aim at the owner: fail their login five
        times and they cannot reach their own engine while a position is open.
        That trade is not worth making.
    """

    per_client_attempts: int = 5
    per_account_attempts: int = 50
    base_lockout_seconds: float = 30.0
    max_lockout_seconds: float = 3600.0
    window_seconds: float = 900.0

    _failures: dict[str, list[float]] = field(default_factory=dict)
    _locked_until: dict[str, float] = field(default_factory=dict)

    def _limit(self, key: str) -> int:
        return (
            self.per_client_attempts if key.startswith("ip:")
            else self.per_account_attempts
        )

    def check(self, *keys: str, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        for key in keys:
            until = self._locked_until.get(key)
            if until is not None and moment < until:
                raise TooManyAttempts(
                    f"too many failed attempts; try again in "
                    f"{int(until - moment) + 1}s"
                )

    def record_failure(self, *keys: str, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        for key in keys:
            recent = [
                t for t in self._failures.get(key, ())
                if moment - t < self.window_seconds
            ]
            recent.append(moment)
            self._failures[key] = recent

            limit = self._limit(key)
            if len(recent) >= limit:
                # Each failure past the threshold doubles the wait. Five
                # guesses cost half a minute; twenty cost an hour.
                over = len(recent) - limit
                lockout = min(
                    self.base_lockout_seconds * (2 ** over),
                    self.max_lockout_seconds,
                )
                self._locked_until[key] = moment + lockout

    def record_success(self, *keys: str) -> None:
        for key in keys:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def locked_out(self, key: str, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        until = self._locked_until.get(key)
        return until is not None and moment < until


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

# Twelve hours: long enough to cover a trading day without a second login,
# short enough that a token found on a lost phone tomorrow is already dead.
DEFAULT_SESSION_TTL = 12 * 3600.0
# And thirty minutes of silence ends it early. Most "stolen session" stories
# are a device left unlocked on a table, not cryptography.
DEFAULT_IDLE_TIMEOUT = 30 * 60.0

# One message for every failure. A form that says "no such user" is a form that
# tells an attacker which name to spend their guesses on.
WRONG_CREDENTIALS = "username or password is wrong"


@dataclass(slots=True)
class LoginService:
    """Exchanges a password for a session token."""

    store: OperatorStore
    tokens: TokenRegistry
    throttle: LoginThrottle = field(default_factory=LoginThrottle)
    session_ttl_seconds: float = DEFAULT_SESSION_TTL

    def login(
        self, username: Any, password: Any, *, client: str = "unknown"
    ) -> AccessToken:
        name = str(username or "").strip().lower()
        secret = str(password or "")
        client_key, account_key = f"ip:{client}", f"user:{name}"

        self.throttle.check(client_key, account_key)

        operator = self.store.get(name)
        if operator is None or operator.disabled:
            # Spend the hashing time anyway. A fast rejection is a working
            # answer to "does this account exist?".
            self.store.hasher.spend_the_same_time(secret)
            self.throttle.record_failure(client_key, account_key)
            raise Unauthorised(WRONG_CREDENTIALS)

        if not self.store.hasher.verify(secret, operator.password_hash):
            self.throttle.record_failure(client_key, account_key)
            raise Unauthorised(WRONG_CREDENTIALS)

        self.throttle.record_success(client_key, account_key)
        token = AccessToken(
            secret=secrets.token_urlsafe(32),
            capabilities=operator.role.capabilities,
            label=operator.username,
            expires_at=time.monotonic() + self.session_ttl_seconds,
        )
        self.tokens.add(token)
        return token

    def logout(self, presented: str | None) -> bool:
        """End one session. Idempotent, and never says whether it existed."""
        token = self.tokens.resolve(presented)
        if token is None:
            return False
        self.tokens.revoke(token)
        return True

    def role_of(self, token: AccessToken) -> Role | None:
        operator = self.store.get(token.label)
        return operator.role if operator else None
