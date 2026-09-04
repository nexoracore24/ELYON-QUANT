"""Can this machine run the engine?

Preflight answers "would starting achieve anything, given the configuration".
This answers the question underneath it: **is this host capable of the job at
all.** They are different questions and they fail at different times -- a
configuration mistake shows up on the first bar, a host mistake shows up as an
import error at 3am, or worse, as an engine that appears to run and quietly
cannot do the one thing it exists for.

The checks here deliberately import nothing from the rest of the engine. A
"will this work here" tool that cannot run until the thing it is checking
imports successfully is not much use on the host where it matters.

The failure this was written for: somebody deploys to a serverless platform.
It builds, it goes green, and none of it can work -- not because of a bug, but
because a trading engine is a stateful, long-lived, Windows-bound process and
a serverless function is none of those things. That deserves a straight answer
rather than a mysterious silence.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

MIN_PYTHON = (3, 11)

# Environment variables each of these platforms sets in its own runtime. A
# process that finds one of them is inside something ephemeral, and no amount
# of configuration makes an ephemeral process able to hold a position.
SERVERLESS_MARKERS: dict[str, str] = {
    "VERCEL": "Vercel",
    "AWS_LAMBDA_FUNCTION_NAME": "AWS Lambda",
    "K_SERVICE": "Google Cloud Run / Cloud Functions",
    "FUNCTIONS_WORKER_RUNTIME": "Azure Functions",
    "NETLIFY": "Netlify Functions",
    "CF_PAGES": "Cloudflare Pages",
    "DENO_DEPLOYMENT_ID": "Deno Deploy",
}


@dataclass(frozen=True, slots=True)
class HostCheck:
    """One finding about the machine, in the same shape as a preflight check."""

    name: str
    passed: bool
    blocking: bool
    detail: str

    @property
    def mark(self) -> str:
        return "✓" if self.passed else ("✕" if self.blocking else "!")

    def __str__(self) -> str:
        return f"{self.mark} {self.name:<14} {self.detail}"


@dataclass(frozen=True, slots=True)
class HostReport:
    checks: tuple[HostCheck, ...]

    @property
    def blockers(self) -> tuple[HostCheck, ...]:
        return tuple(c for c in self.checks if not c.passed and c.blocking)

    @property
    def advisories(self) -> tuple[HostCheck, ...]:
        return tuple(c for c in self.checks if not c.passed and not c.blocking)

    @property
    def can_trade_live(self) -> bool:
        """Whether this host could run a session against a real broker."""
        return not self.blockers

    def __str__(self) -> str:
        return "\n".join(f"  {c}" for c in self.checks)


# ---------------------------------------------------------------------------
# The individual checks
# ---------------------------------------------------------------------------

def _serverless() -> HostCheck:
    """A function that dies after the response cannot hold a position.

    Blocking, and it is the first check because everything below it is a
    consequence. An engine needs to accumulate candles, keep an open position,
    hold a risk budget with outstanding reservations, and poll a feed every
    250ms between requests that nobody is making. None of that survives an
    invocation boundary, and scaling out -- the thing these platforms do
    automatically and well -- means two engines on one account, which is the
    duplicate-position failure the whole OMS exists to prevent.
    """
    for variable, name in SERVERLESS_MARKERS.items():
        if os.environ.get(variable):
            return HostCheck(
                "runtime", False, True,
                f"running inside {name}, which is serverless. The engine is a "
                f"long-lived stateful process: it accumulates candles, holds a "
                f"position, and polls a feed between requests. None of that "
                f"survives a function invocation, and two instances on one "
                f"account is the duplicate-position bug. Use a VPS.",
            )
    return HostCheck("runtime", True, True, "a long-lived process, not a function")


def _python() -> HostCheck:
    version = ".".join(str(p) for p in sys.version_info[:3])
    ok = sys.version_info >= MIN_PYTHON
    return HostCheck(
        "python", ok, True,
        version if ok else
        f"{version}; {'.'.join(str(p) for p in MIN_PYTHON)} or newer is required",
    )


def _operating_system() -> HostCheck:
    """MetaTrader5 is Windows-only, and that is not a packaging accident.

    The package drives a running terminal through a Windows API. There is no
    Linux build, no macOS build, and no amount of Wine that makes it a
    supported configuration. Not blocking on its own -- backtesting,
    calibration and paper trading all work fine anywhere -- but it decides
    whether this host can ever reach a real broker through MT5.
    """
    system = platform.system()
    if system == "Windows":
        return HostCheck("os", True, False, f"{system} {platform.release()}")
    return HostCheck(
        "os", False, False,
        f"{system}: MetaTrader5 is Windows-only, so this host can backtest, "
        f"calibrate and paper-trade but cannot reach a broker through MT5",
    )


def _metatrader() -> HostCheck:
    try:
        import MetaTrader5  # type: ignore  # noqa: F401
    except ImportError:
        return HostCheck(
            "metatrader5", False, False,
            "not installed (pip install MetaTrader5, on Windows)",
        )
    except Exception as exc:  # noqa: BLE001 -- the package misbehaves on import
        return HostCheck("metatrader5", False, False, f"import failed: {exc}")
    return HostCheck("metatrader5", True, False, "installed")


def _timezone_database() -> HostCheck:
    """Killzones are defined in New York local time, and DST is not optional.

    A minimal container image often ships without tzdata, and then
    ``ZoneInfo("America/New_York")`` raises. Every ICT model in the catalog is
    defined by a window in that clock, so this is the difference between a
    session that trades the right hours and one that raises on the first bar
    it evaluates.
    """
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("America/New_York")
    except Exception as exc:  # noqa: BLE001
        return HostCheck(
            "timezones", False, True,
            f"no IANA timezone database ({exc}). Killzones are defined in New "
            f"York local time, so every session-based model needs it. "
            f"Install tzdata (apt install tzdata, or pip install tzdata).",
        )
    return HostCheck("timezones", True, True, "IANA database available")


def _writable(path: Path, label: str, *, blocking: bool) -> HostCheck:
    """Whether the engine can actually write where it is about to write.

    A read-only filesystem is the standard shape of a container image, and it
    turns three separate things into silent no-ops: the order journal that a
    crash recovers from, the account file, and every setting changed from the
    app. The engine reports a failed save rather than hiding it -- but finding
    out at startup is better than finding out from an amber message after you
    have already changed something.
    """
    target = path if path.is_dir() else path.parent
    try:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target):
            pass
    except Exception as exc:  # noqa: BLE001
        return HostCheck(
            label, False, blocking,
            f"cannot write to {target}: {exc}. The order journal, the account "
            f"file and any setting changed from the app all live here.",
        )
    return HostCheck(label, True, blocking, f"{target} is writable")


def _ephemeral_storage(path: Path) -> HostCheck:
    """Writable is not the same as durable.

    ``/tmp`` is writable on nearly everything, including the platforms where
    it is wiped between invocations and is not shared between instances. An
    order journal in ``/tmp`` looks like it is working right up until the
    moment it is needed.
    """
    resolved = (path if path.is_dir() else path.parent).resolve()
    if str(resolved).startswith(("/tmp", "/var/tmp")):
        return HostCheck(
            "durability", False, False,
            f"{resolved} is temporary storage. It is writable, but an order "
            f"journal there is gone exactly when you need it -- after a crash.",
        )
    return HostCheck("durability", True, False, f"{resolved} is durable storage")


def _disk(path: Path) -> HostCheck:
    try:
        free = shutil.disk_usage(path if path.is_dir() else path.parent).free
    except Exception as exc:  # noqa: BLE001
        return HostCheck("disk", False, False, f"could not measure: {exc}")
    megabytes = free // (1024 * 1024)
    # The journal is one JSON line per event. A busy day is single-digit
    # megabytes; anything under 100MB means something else is about to fill up.
    return HostCheck(
        "disk", megabytes >= 100, False,
        f"{megabytes} MB free" + ("" if megabytes >= 100 else " -- getting tight"),
    )


def _outbound_note() -> HostCheck:
    """Stated rather than tested.

    Reaching out to check would either be a false negative behind a proxy or a
    request this tool has no business making from someone's trading host.
    """
    return HostCheck(
        "network", True, False,
        "the terminal talks to the broker, not this process; the control "
        "surface binds to localhost and expects a VPN or SSH tunnel in front",
    )


def inspect(working_directory: str | Path | None = None) -> HostReport:
    """Everything worth knowing about this machine, in one pass."""
    path = Path(working_directory or Path.cwd())
    return HostReport((
        _serverless(),
        _python(),
        _operating_system(),
        _metatrader(),
        _timezone_database(),
        _writable(path, "filesystem", blocking=True),
        _ephemeral_storage(path),
        _disk(path),
        _outbound_note(),
    ))


def verdict(report: HostReport) -> str:
    """What this host can be used for, said plainly."""
    if report.blockers:
        return (
            "This host cannot run the engine.\n\n  "
            + "\n  ".join(c.detail for c in report.blockers)
        )
    windows = any(c.name == "os" and c.passed for c in report.checks)
    mt5 = any(c.name == "metatrader5" and c.passed for c in report.checks)
    if windows and mt5:
        return (
            "This host can run a live session: backtest, calibrate, paper "
            "trade, and reach a broker through MetaTrader 5."
        )
    if windows:
        return (
            "This host can backtest, calibrate and paper trade. For a live "
            "session, install MetaTrader5 and start the terminal."
        )
    return (
        "This host can backtest, calibrate and paper trade. It cannot reach a "
        "broker through MetaTrader 5 -- that needs Windows with the terminal "
        "running. The usual split is a Windows VPS for the engine and this "
        "machine for research."
    )
