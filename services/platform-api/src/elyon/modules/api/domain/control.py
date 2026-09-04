"""Configuring and starting the engine from the app.

The control surface up to now could answer two questions -- what is happening,
and please stop. This is the other half: change the settings, check whether
starting would be sensible, and start.

Three things shape it.

**A rejected change leaves nothing half-applied.** The new configuration is
built and validated in full before anything is swapped. There is no state where
the risk fraction took effect and the strategy list did not, because there is no
moment where one has been written and the other has not.

**Starting is checked, not just permitted.** Pressing Start on an engine that
cannot possibly trade -- no calibrated strategy, a dead feed, LIVE mode against
a paper broker -- produces a bot that sits there looking healthy and does
nothing, which is the failure people waste the most time on. Preflight says what
would stop it, before it starts.

**Every change is recorded with a name on it.** A configuration change is as
consequential as an order: it decides what every later order will be. The
journal says who changed what, and from what to what.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from elyon.modules.session.domain import Mode, SessionConfig, TradingSession
from elyon.modules.session.domain.settings import (
    BY_KEY,
    FLAT_ONLY_KEYS,
    RESTART_KEYS,
    Scope,
    apply_changes,
    describe,
)
from elyon.shared_kernel.edcs.numeric import DeterminismError

# Typed out, not toggled. The one action in the whole surface that changes
# whether losses are real, and a switch that can be brushed is not the right
# shape for it.
LIVE_CONFIRMATION = "TRADE REAL MONEY"


@dataclass(frozen=True, slots=True)
class ConfigChange:
    """One applied edit, as the journal records it."""

    at: str
    who: str
    key: str
    before: Any
    after: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at, "who": self.who, "key": self.key,
            "before": self.before, "after": self.after,
        }

    def __str__(self) -> str:
        return f"{self.who} set {self.key}: {self.before!r} -> {self.after!r}"


@dataclass(frozen=True, slots=True)
class Check:
    """One preflight finding."""

    name: str
    passed: bool
    blocking: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "passed": self.passed,
            "blocking": self.blocking, "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class Preflight:
    """Whether starting would do anything, and what would stop it."""

    checks: tuple[Check, ...]

    @property
    def blockers(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.passed and c.blocking)

    @property
    def advisories(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.passed and not c.blocking)

    @property
    def can_start(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "canStart": self.can_start,
            "checks": [c.to_dict() for c in self.checks],
            "blockers": [c.detail for c in self.blockers],
            "advisories": [c.detail for c in self.advisories],
        }


def preflight(
    session: TradingSession, *, feed_state: str | None = None
) -> Preflight:
    """What would stop this engine from trading if it were started now.

    Split into blocking and advisory by one question: *would starting be a
    mistake, or merely quiet?* A LIVE session pointed at a paper broker is a
    mistake. A PAPER session with nothing calibrated is quiet, and quiet is how
    a strategy earns a tier.
    """
    config = session.config
    checks: list[Check] = []

    uncalibrated = config.uncalibrated_live()
    fully_uncalibrated = len(uncalibrated) == len(config.strategies)
    checks.append(Check(
        "calibration",
        passed=not fully_uncalibrated or config.allow_uncalibrated_live,
        # In LIVE this is a mistake: real money behind evidence nobody has
        # measured. Anywhere else it is exactly how evidence gets measured.
        blocking=config.mode.touches_real_money,
        detail=(
            f"every live strategy is uncalibrated "
            f"({', '.join(s.value for s in uncalibrated)}); none of them can "
            f"open a trade alone, so this session will take no trades"
            if fully_uncalibrated else
            "at least one live strategy has measured evidence behind it"
        ),
    ))

    broker = type(session.broker).__name__
    real_broker = broker not in ("PaperBroker", "FakeBroker")
    checks.append(Check(
        "broker",
        passed=(not config.mode.touches_real_money) or real_broker,
        blocking=True,
        detail=(
            f"LIVE mode with a {broker}: orders would be simulated while every "
            f"report claims they are real"
            if config.mode.touches_real_money and not real_broker
            else f"{broker}"
        ),
    ))

    checks.append(Check(
        "feed",
        passed=feed_state in (None, "LIVE", "STARTING"),
        # A dead feed on a LIVE session is a blocker; on a replay it just means
        # the file ran out.
        blocking=config.mode.touches_real_money,
        detail=(
            f"the market data feed is {feed_state}"
            if feed_state not in (None, "LIVE", "STARTING")
            else f"feed {feed_state or 'not attached'}"
        ),
    ))

    dead_letters = len(session.oms.dlq)
    checks.append(Check(
        "deadLetters",
        passed=dead_letters == 0,
        blocking=False,
        detail=(
            f"{dead_letters} event(s) in the dead letter queue from a previous "
            f"run; starting does not clear them"
            if dead_letters else "no dead letters"
        ),
    ))

    checks.append(Check(
        "calendar",
        passed=config.calendar_path is not None,
        blocking=False,
        detail=(
            "no economic calendar; news risk is unknown, so the context score "
            "cannot exceed 92/100"
            if config.calendar_path is None else config.calendar_path
        ),
    ))

    return Preflight(tuple(checks))


@dataclass(slots=True)
class EngineControl:
    """Everything the app can do to a running engine.

    Holds no state of its own beyond the audit trail. The session is reached
    through ``read`` and ``write`` callables so that a live engine's lock is
    honoured without this module knowing there is one -- and so a test can drive
    the whole surface with nothing but a session.
    """

    read: Callable[[Callable[[TradingSession], Any]], Any]
    write: Callable[[Callable[[TradingSession], Any]], Any]
    feed_state: Callable[[], str | None] = lambda: None
    start_feed: Callable[[], None] | None = None
    stop_feed: Callable[[], None] | None = None
    journal: list[ConfigChange] = field(default_factory=list)
    # Where an applied change is written so it survives a restart, and where a
    # record of who changed what is appended. Both optional; both reported
    # honestly when absent, because a setting that takes effect and then
    # reverts on the next reboot is worse than one that was refused.
    persist: Callable[[SessionConfig], None] | None = None
    record: Callable[[ConfigChange], None] | None = None

    # -- reading ----------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        """Every setting, its value, and when it may be changed."""
        config: SessionConfig = self.read(lambda s: s.config)
        exposed = self.read(lambda s: s.position is not None)
        entries = describe(config)
        for entry in entries:
            # Editability is answered here rather than left to the page to work
            # out, so a control that would be refused is never offered.
            scope = Scope(entry["scope"])
            blocked_now = scope is Scope.FLAT_ONLY and exposed
            entry["editable"] = scope is not Scope.RESTART and not blocked_now
            if scope is Scope.RESTART:
                entry["blockedBecause"] = (
                    "the history already accumulated belongs to this value"
                )
            elif blocked_now:
                entry["blockedBecause"] = (
                    "a position is open; changing this would restate a trade "
                    "already sized"
                )
        return {
            "settings": entries,
            "configHash": config.config_hash,
            "warnings": list(config.warnings()),
            "positionOpen": exposed,
            "liveConfirmation": LIVE_CONFIRMATION,
            "recentChanges": [c.to_dict() for c in self.journal[-20:]],
        }

    def preflight(self) -> Preflight:
        return self.read(
            lambda s: preflight(s, feed_state=self.feed_state())
        )

    # -- writing ----------------------------------------------------------

    def apply(
        self,
        changes: Mapping[str, Any],
        *,
        who: str,
        confirmation: str = "",
    ) -> dict[str, Any]:
        """Validate, then swap. Never the other way round."""
        if not changes:
            raise DeterminismError("no changes given")

        self._require_live_confirmation(changes, confirmation)

        current: SessionConfig = self.read(lambda s: s.config)
        candidate, touched = apply_changes(current, changes)
        if not touched:
            return {"changed": [], "message": "nothing changed"}

        before = {key: BY_KEY[key].read(current) for key in touched}

        # The session's own check is the one that counts. This can only refuse
        # earlier or more politely; it cannot permit something the domain
        # forbids.
        changed = self.write(lambda s: s.reconfigure(candidate))

        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        after: SessionConfig = self.read(lambda s: s.config)
        for key in changed:
            self._remember(ConfigChange(
                at=stamp, who=who, key=key,
                before=before.get(key), after=BY_KEY[key].read(after),
            ))

        saved, note = self._save(after)
        return {
            "changed": list(changed),
            "configHash": after.config_hash,
            "warnings": list(after.warnings()),
            "saved": saved,
            "message": f"{len(changed)} setting(s) applied" + note,
        }

    def _save(self, config: SessionConfig) -> tuple[bool, str]:
        """Write the configuration out, and be loud when that fails.

        The change is already live at this point -- the session accepted it.
        What is at stake is whether it is still there after a restart, and a
        setting that silently reverts on the next reboot is the worst kind of
        bug: the engine comes back looking right and sized against something
        else. So a failed write is stated in the same breath as the success,
        rather than logged where nobody is looking.
        """
        if self.persist is None:
            return False, (
                ". Not saved: this engine was started without a config file to "
                "write back to, so the change lasts until it restarts."
            )
        try:
            self.persist(config)
        except Exception as exc:  # noqa: BLE001 -- any filesystem failure
            return False, (
                f". APPLIED BUT NOT SAVED ({exc}). It is live now and will be "
                f"gone after a restart."
            )
        return True, " and saved"

    def _remember(self, change: ConfigChange) -> None:
        self.journal.append(change)
        if self.record is None:
            return
        try:
            self.record(change)
        except Exception as exc:  # noqa: BLE001
            # An unwritable audit log must not undo a change that already took
            # effect -- that would leave the engine and its record disagreeing
            # in the one direction that cannot be reconstructed afterwards.
            print(f"could not record config change: {exc}")

    def _require_live_confirmation(
        self, changes: Mapping[str, Any], confirmation: str
    ) -> None:
        """Switching to LIVE is typed out, not clicked.

        Every other setting can be walked back by setting it again. This one
        cannot: an order that reached a real broker is not undone by changing
        the mode afterwards.
        """
        if str(changes.get("mode", "")).upper() != Mode.LIVE.value:
            return
        current: SessionConfig = self.read(lambda s: s.config)
        if current.mode is Mode.LIVE:
            return
        if confirmation.strip() != LIVE_CONFIRMATION:
            raise DeterminismError(
                f"switching to LIVE sends orders to a real broker. Type "
                f"{LIVE_CONFIRMATION!r} to confirm."
            )

    # -- starting and stopping --------------------------------------------

    def start(self, *, who: str, force: bool = False) -> dict[str, Any]:
        """Begin trading.

        Refuses on a blocking preflight finding unless somebody overrides it
        deliberately. The override exists because a check can be wrong and an
        engine that cannot be started is worse than one that warns; it is
        recorded when used, because "we forced it" is the first question after
        a bad day.
        """
        report = self.preflight()
        if report.blockers and not force:
            return {
                "started": False,
                "preflight": report.to_dict(),
                "message": "preflight failed: " + "; ".join(
                    c.detail for c in report.blockers
                ),
            }

        if self.start_feed is not None:
            self.start_feed()

        note = f"started by {who}" + (" (preflight overridden)" if force and report.blockers else "")
        self.write(lambda s: s.oms.resume(note))

        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._remember(ConfigChange(
            at=stamp, who=who, key="engine",
            before="halted", after="running",
        ))
        return {
            "started": True,
            "preflight": report.to_dict(),
            "message": note + (
                ". Nothing will be traded until a bar closes."
            ),
        }

    def stop(self, *, who: str, reason: str = "") -> dict[str, Any]:
        """Stop trading. Open positions are protected, never closed.

        Closing on a stop would make the safest button in the app the one that
        realises a loss at whatever price happens to be showing. Stopping means
        "open nothing new"; getting out of a trade is a separate decision, taken
        with the chart in front of you.
        """
        note = reason.strip() or f"stopped by {who}"
        self.write(lambda s: s.oms.halt(note))
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._remember(ConfigChange(
            at=stamp, who=who, key="engine", before="running", after="halted",
        ))
        exposed = self.read(lambda s: s.position is not None)
        return {
            "halted": True,
            "message": note + (
                ". A position is still open and is being managed to its stop "
                "or target."
                if exposed else
                ". Nothing is open."
            ),
        }


def config_writer(path: str | Path) -> Callable[[SessionConfig], None]:
    """Persist an applied configuration back to the file it came from.

    Deliberately the same file the engine was started with, rather than a
    parallel "runtime settings" store. Two places to look for what an engine is
    configured to do is one place too many, and the second one is always the
    one that is out of date.
    """
    target = Path(path)

    def write(config: SessionConfig) -> None:
        config.save(target)

    return write


def change_recorder(path: str | Path) -> Callable[[ConfigChange], None]:
    """Append configuration changes to a log, one JSON object per line.

    Same format and the same append-only discipline as the order journal,
    because the question people actually ask is ordered: *what was it set to
    when that trade happened?* An audit trail in a different shape, in a
    different place, is one nobody lines up against the orders.
    """
    target = Path(path)

    def record(change: ConfigChange) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as handle:
            handle.write(json.dumps(change.to_dict(), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    return record


def control_for(session: TradingSession, **wiring: Any) -> EngineControl:
    """Drive a session directly. For a single thread -- a test, a dry run."""
    return EngineControl(
        read=lambda view: view(session),
        write=lambda action: action(session),
        **wiring,
    )


def live_control_for(runner: Any, **wiring: Any) -> EngineControl:
    """Drive a session that a feed thread is also writing to.

    Reads and writes both go through the runner's lock. Reading the session
    directly here would be the obvious shortcut and would hand the app a
    configuration snapshot taken halfway through a swap.
    """
    return EngineControl(
        read=runner.read,
        write=runner.mutate,
        feed_state=lambda: runner.state.value,
        start_feed=lambda: None if runner.running else runner.start(),
        stop_feed=runner.stop,
        **wiring,
    )
