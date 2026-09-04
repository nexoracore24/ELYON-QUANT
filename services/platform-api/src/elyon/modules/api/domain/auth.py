"""Who may do what, over the network.

Exposing a trading engine to a phone means exposing it to whoever holds the
token, and the design follows from one asymmetry:

    **Stopping is safe. Starting is not.**

A stolen phone, a leaked token, a shoulder-surfed QR code -- any of these should
be able to *halt* the bot and *close* positions. That is annoying at worst. None
of them should be able to resume trading, raise the risk per trade, switch to
LIVE, or enable a strategy. Those are the actions that lose money, and they stay
on the machine.

So capabilities are graded by which direction they move risk, not by how
"administrative" they feel:

*   ``OBSERVE`` -- look. Cannot change anything.
*   ``PROTECT`` -- halt, flatten. Only ever *reduces* exposure.
*   ``COMMAND`` -- resume, reconfigure. Only ever *increases* it, and is off
    unless someone deliberately turns it on.

A phone gets OBSERVE and PROTECT. COMMAND exists for completeness and for a
laptop on the same VPN; granting it to a device you carry through airports is a
decision, and the code makes you take it on purpose.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from elyon.shared_kernel.edcs.numeric import DeterminismError

MIN_TOKEN_LENGTH = 32


class Capability(str, Enum):
    """Graded by the direction each action moves risk."""

    OBSERVE = "OBSERVE"
    PROTECT = "PROTECT"
    COMMAND = "COMMAND"

    @property
    def reduces_risk(self) -> bool:
        return self is not Capability.COMMAND


# What a phone gets by default. Everything it can do makes the account safer.
PHONE_CAPABILITIES: frozenset[Capability] = frozenset(
    {Capability.OBSERVE, Capability.PROTECT}
)
READ_ONLY: frozenset[Capability] = frozenset({Capability.OBSERVE})


@dataclass(frozen=True, slots=True)
class AccessToken:
    """One credential and what it may do."""

    secret: str
    capabilities: frozenset[Capability] = PHONE_CAPABILITIES
    label: str = "device"
    # A monotonic deadline, so a clock adjustment cannot extend a session.
    # ``None`` is a token issued at startup and printed once: it lives as long
    # as the process does, which is a decision someone took at a terminal.
    # Tokens minted by a login always carry one.
    expires_at: float | None = None

    def __post_init__(self) -> None:
        if len(self.secret) < MIN_TOKEN_LENGTH:
            raise DeterminismError(
                f"token for {self.label!r} is {len(self.secret)} characters; "
                f"at least {MIN_TOKEN_LENGTH} are required. A short token on "
                f"an endpoint that can flatten positions is worth guessing."
            )
        if not self.capabilities:
            raise DeterminismError(
                f"token for {self.label!r} grants nothing; it would only ever "
                f"return 403"
            )

    def allows(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @property
    def can_command(self) -> bool:
        return Capability.COMMAND in self.capabilities

    @property
    def token_id(self) -> str:
        """A handle for this credential that is safe to log.

        Revoking a session and writing down who did what both need a name for a
        token. The secret is not that name: anything written down often enough
        eventually ends up somewhere it was not meant to be.
        """
        return hashlib.sha256(self.secret.encode()).hexdigest()[:16]

    def is_expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (time.monotonic() if now is None else now) >= self.expires_at

    def seconds_remaining(self, now: float | None = None) -> int | None:
        if self.expires_at is None:
            return None
        moment = time.monotonic() if now is None else now
        return max(0, int(self.expires_at - moment))

    def __str__(self) -> str:
        granted = ", ".join(sorted(c.value for c in self.capabilities))
        return f"{self.label}: {granted}"

    def __repr__(self) -> str:
        # The secret never appears in a repr, a log line or a traceback. It
        # leaks from exactly one of those eventually if it is allowed to.
        return f"AccessToken(label={self.label!r}, secret=<redacted>)"


def new_secret() -> str:
    """A token nobody has to invent.

    ``token_urlsafe`` rather than anything memorable: a credential a person can
    remember is a credential a person can guess.
    """
    return secrets.token_urlsafe(32)


@dataclass(slots=True)
class TokenRegistry:
    """The tokens this server accepts.

    Login sessions and startup tokens live in the same registry on purpose.
    There is exactly one place that decides whether a credential is currently
    good, so a session that has expired cannot survive by arriving through a
    different door.
    """

    tokens: list[AccessToken] = field(default_factory=list)
    # Silence ends a session early. Most stolen-session stories are a device
    # left unlocked on a table, not cryptography. ``None`` disables it, which
    # is right for a token someone deliberately printed and pinned open.
    idle_timeout_seconds: float | None = None
    _last_seen: dict[str, float] = field(default_factory=dict)

    def add(self, token: AccessToken) -> None:
        self.tokens.append(token)
        self._last_seen[token.token_id] = time.monotonic()

    def revoke(self, token: AccessToken) -> None:
        self.tokens = [t for t in self.tokens if t.token_id != token.token_id]
        self._last_seen.pop(token.token_id, None)

    def revoke_all(self, label: str) -> int:
        """End every session belonging to one operator.

        The action a person actually wants after losing a phone, and the reason
        a session token is worth having at all: a password cannot be recalled,
        a session can.
        """
        doomed = [t for t in self.tokens if t.label == label]
        for token in doomed:
            self.revoke(token)
        return len(doomed)

    def _find(self, presented: str) -> AccessToken | None:
        """Compare against every candidate, in constant time.

        ``hmac.compare_digest`` on every token rather than a dictionary lookup:
        comparing strings with ``==`` returns as soon as it finds a difference,
        and the timing of that says how much of the prefix was right. It is a
        slow way to guess a token, and slow is not never.
        """
        found: AccessToken | None = None
        for token in self.tokens:
            if hmac.compare_digest(token.secret, presented):
                found = token
        return found

    def resolve(self, presented: str | None) -> AccessToken | None:
        if not presented:
            return None
        found = self._find(presented)
        if found is None:
            return None
        if self._is_stale(found):
            self.revoke(found)
            return None
        self._last_seen[found.token_id] = time.monotonic()
        return found

    def _is_stale(self, token: AccessToken) -> bool:
        if token.is_expired():
            return True
        if self.idle_timeout_seconds is None:
            return False
        last = self._last_seen.get(token.token_id)
        if last is None:
            return False
        return time.monotonic() - last > self.idle_timeout_seconds

    def authorise(
        self, presented: str | None, needed: Capability
    ) -> AccessToken:
        # Resolved in two steps so an expired session can say so. Telling the
        # holder of a credential that *was* valid that it has run out is not a
        # leak, and "sign in again" is a far better answer than "unknown token"
        # for the one person who will see it most.
        matched = self._find(presented) if presented else None
        if matched is not None and self._is_stale(matched):
            self.revoke(matched)
            raise Unauthorised("session expired; sign in again")

        token = self.resolve(presented)
        if token is None:
            raise Unauthorised("unknown or missing token")
        if not token.allows(needed):
            raise Forbidden(
                f"{token.label} may not {needed.value.lower()}"
                + (
                    ". Starting, reconfiguring and raising risk need COMMAND, "
                    "which only an OWNER holds. Stopping does not, and never "
                    "will."
                    if needed is Capability.COMMAND else ""
                )
            )
        return token

    def __len__(self) -> int:
        return len(self.tokens)

    def summary(self) -> str:
        return "\n".join(f"  {token}" for token in self.tokens)


class Unauthorised(Exception):
    """No usable credential was presented."""


class Forbidden(Exception):
    """A valid credential that does not cover this action."""


def phone_token(label: str = "phone") -> AccessToken:
    """A token for a device you carry: observe and protect, nothing more."""
    return AccessToken(new_secret(), PHONE_CAPABILITIES, label)


def command_token(label: str = "console") -> AccessToken:
    """Full control. For a machine that stays where you left it.

    Deliberately a separate function rather than a flag, so granting COMMAND is
    something written down rather than something defaulted into.
    """
    return AccessToken(
        new_secret(),
        frozenset({Capability.OBSERVE, Capability.PROTECT, Capability.COMMAND}),
        label,
    )
