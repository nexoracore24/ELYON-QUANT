"""What may be changed on a running engine, and when.

A configuration file is edited while nothing is running, so every setting is
equally safe to write. A *running* engine is not like that. Some settings are
read fresh on every bar and take effect immediately; some are baked into objects
built once at startup; and some describe the position that is open right now.

Changing one of the last kind mid-flight does not "reconfigure" anything -- it
rewrites the meaning of a trade that is already on. If a position was sized
against £10,000 of equity and a 0.5% risk budget, editing equity to £50,000
while it is open does not resize it: it just makes every number reported about
it a lie.

So each setting declares a **scope**, and the scope is data rather than
etiquette:

*   ``LIVE`` -- read on the next bar. Safe to change with a position open,
    because it only affects what happens next.
*   ``FLAT_ONLY`` -- allowed only with no position open. These change what a
    number *means*, and the engine cannot retroactively agree.
*   ``RESTART`` -- baked into the candle builder, the ATR window, the warm-up.
    Changing them needs a new session, not a new value, because the accumulated
    history belongs to the old ones.

This table is the single source of truth. The control surface presents it; it
does not have a second opinion about it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Iterable, Mapping

from elyon.modules.strategy.domain import ConflictPolicy, StrategyId
from elyon.modules.trading.domain.position import ManagementPolicy
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

from .config import Mode, RiskSettings, SessionConfig


class Scope(str, Enum):
    """When a setting may be changed."""

    LIVE = "LIVE"
    FLAT_ONLY = "FLAT_ONLY"
    RESTART = "RESTART"

    @property
    def explanation(self) -> str:
        return {
            Scope.LIVE: "takes effect on the next bar",
            Scope.FLAT_ONLY: "only while no position is open",
            Scope.RESTART: "needs a new session; the history belongs to the old value",
        }[self]


class Kind(str, Enum):
    """How a value is typed, so the page can render the right control."""

    DECIMAL = "decimal"
    PERCENT = "percent"       # a decimal shown as %, stored as a fraction
    INT = "int"
    BOOL = "bool"
    CHOICE = "choice"
    STRATEGIES = "strategies"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class Setting:
    """One thing a person may change, and everything needed to change it."""

    key: str
    label: str
    scope: Scope
    kind: Kind
    help: str
    read: Callable[[SessionConfig], Any]
    write: Callable[[SessionConfig, Any], SessionConfig]
    choices: tuple[str, ...] = ()
    # Settings that can only ever increase exposure. The surface asks for these
    # to be confirmed rather than toggled.
    dangerous: bool = False

    def describe(self, config: SessionConfig) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "scope": self.scope.value,
            "scopeNote": self.scope.explanation,
            "kind": self.kind.value,
            "help": self.help,
            "choices": list(self.choices),
            "dangerous": self.dangerous,
            "value": self.read(config),
        }


# ---------------------------------------------------------------------------
# Readers and writers
#
# Each pair is trivial on its own. Keeping them next to the scope is the point:
# a setting cannot be added without someone deciding when it may be changed.
# ---------------------------------------------------------------------------

def _risk(config: SessionConfig, **changes: Any) -> SessionConfig:
    return replace(config, risk=replace(config.risk, **changes))


def _management(config: SessionConfig, **changes: Any) -> SessionConfig:
    return replace(config, management=replace(config.management, **changes))


def _instrument(config: SessionConfig, **changes: Any) -> SessionConfig:
    return replace(config, instrument=replace(config.instrument, **changes))


def _as_strategies(value: Any) -> tuple[StrategyId, ...]:
    if isinstance(value, str):
        raise DeterminismError(
            "a strategy list must be a list, not a string; "
            f"got {value!r}"
        )
    resolved: list[StrategyId] = []
    for name in value or ():
        try:
            resolved.append(StrategyId(str(name)))
        except ValueError as exc:
            known = ", ".join(s.value for s in StrategyId)
            raise DeterminismError(
                f"unknown strategy {name!r}. Known strategies: {known}"
            ) from exc
    return tuple(dict.fromkeys(resolved))  # de-duplicated, order preserved


def _as_decimal(value: Any) -> Decimal:
    # str() first: a JSON body arrives with floats in it, and dec() refuses a
    # float on purpose. Rounding a risk fraction on its way in would make the
    # number the engine used differ from the number the person typed.
    return dec(str(value))


def _as_optional_decimal(value: Any) -> Decimal | None:
    if value in (None, "", "none", "null"):
        return None
    return _as_decimal(value)


def _as_int(value: Any) -> int:
    return int(str(value))


def _as_optional_int(value: Any) -> int | None:
    if value in (None, "", "none", "null"):
        return None
    return _as_int(value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _str_or_none(value: Decimal | int | None) -> str | None:
    return None if value is None else str(value)


SETTINGS: tuple[Setting, ...] = (
    # -- what it trades ---------------------------------------------------
    Setting(
        "strategies", "Live strategies", Scope.LIVE, Kind.STRATEGIES,
        "Strategies allowed to open a trade. Uncalibrated ones still cannot "
        "act alone, however many are enabled.",
        read=lambda c: sorted(s.value for s in c.strategies),
        write=lambda c, v: replace(c, strategies=_as_strategies(v)),
        choices=tuple(s.value for s in StrategyId),
    ),
    Setting(
        "shadowStrategies", "Shadow strategies", Scope.LIVE, Kind.STRATEGIES,
        "Evaluated and recorded, never traded. This is how a strategy earns a "
        "tier without costing anything.",
        read=lambda c: sorted(s.value for s in c.shadow_strategies),
        write=lambda c, v: replace(c, shadow_strategies=_as_strategies(v)),
        choices=tuple(s.value for s in StrategyId),
    ),
    Setting(
        "conflictPolicy", "When strategies disagree", Scope.LIVE, Kind.CHOICE,
        "VETO means one disagreement is enough to stand aside. Averaging "
        "disagreement is how a system takes the trades nobody believed in.",
        read=lambda c: c.conflict_policy.value,
        write=lambda c, v: replace(c, conflict_policy=ConflictPolicy(str(v))),
        choices=tuple(p.value for p in ConflictPolicy),
    ),
    Setting(
        "entryScoreThreshold", "Entry score threshold", Scope.LIVE, Kind.INT,
        "Minimum score out of 100 to take a setup. Empty uses the tier's own "
        "threshold.",
        read=lambda c: c.entry_score_threshold,
        write=lambda c, v: replace(c, entry_score_threshold=_as_optional_int(v)),
    ),

    # -- how much it risks -------------------------------------------------
    Setting(
        "riskPerTrade", "Risk per trade", Scope.LIVE, Kind.PERCENT,
        "Fraction of equity risked between entry and stop. Hard-capped at 5%: "
        "above that a normal losing streak is a blow-up, not a drawdown.",
        read=lambda c: str(c.risk.risk_per_trade),
        write=lambda c, v: _risk(c, risk_per_trade=_as_decimal(v)),
    ),
    Setting(
        "minRewardRisk", "Minimum reward:risk", Scope.LIVE, Kind.DECIMAL,
        "Setups paying less than this are refused after everything else "
        "passed. Risk has the last word.",
        read=lambda c: str(c.risk.min_reward_risk),
        write=lambda c, v: _risk(c, min_reward_risk=_as_decimal(v)),
    ),
    Setting(
        "equity", "Account equity", Scope.FLAT_ONLY, Kind.DECIMAL,
        "The base every risk figure is a fraction of. Changing it with a "
        "position open would restate a size that was already chosen.",
        read=lambda c: str(c.risk.equity),
        write=lambda c, v: _risk(c, equity=_as_decimal(v)),
    ),
    Setting(
        "dailyLossLimit", "Daily loss limit", Scope.FLAT_ONLY, Kind.PERCENT,
        "The day stops here. Must exceed the risk of a single trade, or the "
        "first loss ends the day.",
        read=lambda c: str(c.risk.daily_loss_limit),
        write=lambda c, v: _risk(c, daily_loss_limit=_as_decimal(v)),
    ),
    Setting(
        "maxOpenRisk", "Maximum open risk", Scope.FLAT_ONLY, Kind.PERCENT,
        "Total risk allowed on the table at once.",
        read=lambda c: str(c.risk.max_open_risk),
        write=lambda c, v: _risk(c, max_open_risk=_as_decimal(v)),
    ),

    # -- what the broker will accept ---------------------------------------
    # These convert a risk fraction into lots. Left at a standard FX lot they
    # are simply wrong for gold, an index or a crypto pair -- and wrong here
    # means every trade on that instrument is the wrong size, quietly, with
    # nothing in the logs looking unusual.
    Setting(
        "lotStep", "Lot step", Scope.FLAT_ONLY, Kind.DECIMAL,
        "The smallest increment the broker will accept. Sizes are rounded "
        "down to it, never up.",
        read=lambda c: str(c.instrument.lot_step),
        write=lambda c, v: _instrument(c, lot_step=_as_decimal(v)),
    ),
    Setting(
        "minLot", "Minimum lot", Scope.FLAT_ONLY, Kind.DECIMAL,
        "Below this the broker refuses. A setup that sizes smaller is skipped "
        "rather than rounded up into more risk than was budgeted.",
        read=lambda c: str(c.instrument.min_lot),
        write=lambda c, v: _instrument(c, min_lot=_as_decimal(v)),
    ),
    Setting(
        "maxLot", "Maximum lot", Scope.FLAT_ONLY, Kind.DECIMAL,
        "The broker's ceiling for one order.",
        read=lambda c: str(c.instrument.max_lot),
        write=lambda c, v: _instrument(c, max_lot=_as_decimal(v)),
    ),
    Setting(
        "valuePerPriceUnit", "Value per price unit", Scope.FLAT_ONLY,
        Kind.DECIMAL,
        "Account currency per 1.0 of price, per lot. 100000 for a standard FX "
        "lot; check your broker's contract size for anything else, because "
        "this number is what turns a stop distance into a position size.",
        read=lambda c: str(c.instrument.value_per_price_unit),
        write=lambda c, v: _instrument(c, value_per_price_unit=_as_decimal(v)),
    ),

    # -- how it manages what it holds --------------------------------------
    Setting(
        "breakEvenAtR", "Break even at", Scope.LIVE, Kind.DECIMAL,
        "Move the stop to entry once the trade is this far in profit, in R. "
        "Empty never breaks even.",
        read=lambda c: _str_or_none(c.management.break_even_at_r),
        write=lambda c, v: _management(c, break_even_at_r=_as_optional_decimal(v)),
    ),
    Setting(
        "trailFromR", "Start trailing at", Scope.LIVE, Kind.DECIMAL,
        "Where the trailing stop begins, in R. A stop never moves backwards.",
        read=lambda c: _str_or_none(c.management.trail_from_r),
        write=lambda c, v: _management(c, trail_from_r=_as_optional_decimal(v)),
    ),
    Setting(
        "partialAtR", "Take a partial at", Scope.LIVE, Kind.DECIMAL,
        "Where part of the position comes off, in R. Empty takes none.",
        read=lambda c: _str_or_none(c.management.partial_at_r),
        write=lambda c, v: _management(c, partial_at_r=_as_optional_decimal(v)),
    ),
    Setting(
        "partialFraction", "Partial size", Scope.LIVE, Kind.PERCENT,
        "How much of the position the partial takes.",
        read=lambda c: str(c.management.partial_fraction),
        write=lambda c, v: _management(c, partial_fraction=_as_decimal(v)),
    ),
    Setting(
        "timeStopBars", "Time stop", Scope.LIVE, Kind.INT,
        "A trade that has gone nowhere in this many bars is released. Capital "
        "sitting in a dead setup is capital not available to a live one.",
        read=lambda c: c.management.time_stop_bars,
        write=lambda c, v: _management(c, time_stop_bars=_as_optional_int(v)),
    ),

    # -- how it reads the market -------------------------------------------
    Setting(
        "swingGrade", "Swing grade", Scope.LIVE, Kind.INT,
        "How significant a swing has to be before structure counts it.",
        read=lambda c: c.swing_grade,
        write=lambda c, v: replace(c, swing_grade=_as_int(v)),
    ),
    Setting(
        "lookbackBars", "Lookback", Scope.LIVE, Kind.INT,
        "How far back each bar's analysis reaches.",
        read=lambda c: c.lookback_bars,
        write=lambda c, v: replace(c, lookback_bars=_as_int(v)),
    ),

    # -- the dangerous end -------------------------------------------------
    Setting(
        "mode", "Mode", Scope.FLAT_ONLY, Kind.CHOICE,
        "LIVE sends orders to a real broker. Everything else is a rehearsal.",
        read=lambda c: c.mode.value,
        write=lambda c, v: replace(c, mode=Mode(str(v))),
        choices=(Mode.PAPER.value, Mode.LIVE.value),
        dangerous=True,
    ),
    Setting(
        "allowUncalibratedLive", "Allow uncalibrated strategies to trade",
        Scope.FLAT_ONLY, Kind.BOOL,
        "Lets a strategy with no measured evidence open a trade on its own. "
        "This is the setting that turns a backtested system into a guess.",
        read=lambda c: c.allow_uncalibrated_live,
        write=lambda c, v: replace(c, allow_uncalibrated_live=_as_bool(v)),
        dangerous=True,
    ),
    Setting(
        "skipContextGate", "Skip the context gate", Scope.FLAT_ONLY, Kind.BOOL,
        "Look for entries in any market condition. A research affordance, "
        "refused outright in LIVE mode.",
        read=lambda c: c.skip_context_gate,
        write=lambda c, v: replace(c, skip_context_gate=_as_bool(v)),
        dangerous=True,
    ),

    # -- what a restart owns ----------------------------------------------
    Setting(
        "symbol", "Symbol", Scope.RESTART, Kind.TEXT,
        "Every candle, swing and ATR value accumulated so far belongs to this "
        "symbol.",
        read=lambda c: c.symbol,
        write=lambda c, v: replace(c, symbol=str(v).strip().upper()),
    ),
    Setting(
        "timeframe", "Timeframe", Scope.RESTART, Kind.TEXT,
        "The candle builder is built around it; the bars already closed cannot "
        "be re-cut.",
        read=lambda c: c.timeframe,
        write=lambda c, v: replace(c, timeframe=str(v).strip().upper()),
    ),
    Setting(
        "atrPeriod", "ATR period", Scope.RESTART, Kind.INT,
        "The ATR is a running value; changing its window mid-stream would give "
        "a number that is neither the old one nor the new one.",
        read=lambda c: c.atr_period,
        write=lambda c, v: replace(c, atr_period=_as_int(v)),
    ),
    Setting(
        "warmupBars", "Warm-up bars", Scope.RESTART, Kind.INT,
        "How many bars before the engine is allowed an opinion.",
        read=lambda c: c.warmup_bars,
        write=lambda c, v: replace(c, warmup_bars=_as_int(v)),
    ),
    Setting(
        "calendar", "Economic calendar", Scope.RESTART, Kind.TEXT,
        "Path to the event file. Without one, NEWS_CLEAR is withheld and the "
        "context score cannot exceed 92/100.",
        read=lambda c: c.calendar_path or "",
        write=lambda c, v: replace(c, calendar_path=(str(v).strip() or None)),
    ),
)

BY_KEY: Mapping[str, Setting] = {s.key: s for s in SETTINGS}

# The fields a running session has baked into objects it built once. Kept
# beside the table so the two cannot drift apart.
RESTART_KEYS: frozenset[str] = frozenset(
    s.key for s in SETTINGS if s.scope is Scope.RESTART
)
FLAT_ONLY_KEYS: frozenset[str] = frozenset(
    s.key for s in SETTINGS if s.scope is Scope.FLAT_ONLY
)


def describe(config: SessionConfig) -> list[dict[str, Any]]:
    """Every setting, its scope, and what it is currently set to."""
    return [setting.describe(config) for setting in SETTINGS]


def apply_changes(
    config: SessionConfig, changes: Mapping[str, Any]
) -> tuple[SessionConfig, tuple[str, ...]]:
    """Build the configuration these changes describe.

    Nothing is mutated: a new :class:`SessionConfig` is constructed and its own
    validation decides whether it is coherent. A change that would produce an
    impossible configuration fails here, before anything has been swapped, so a
    rejected edit leaves the running engine exactly as it was.

    Unknown keys are an error rather than being ignored -- a typo that silently
    leaves a setting untouched is a change someone believes they made.
    """
    unknown = set(changes) - set(BY_KEY)
    if unknown:
        raise DeterminismError(
            f"unknown setting(s): {', '.join(sorted(unknown))}. "
            f"Known settings: {', '.join(sorted(BY_KEY))}"
        )

    updated = config
    changed: list[str] = []
    for key, value in changes.items():
        setting = BY_KEY[key]
        before = setting.read(updated)
        try:
            candidate = setting.write(updated, value)
        except DeterminismError:
            raise
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise DeterminismError(
                f"{setting.label} rejected {value!r}: {exc}"
            ) from exc
        if setting.read(candidate) != before:
            changed.append(key)
        updated = candidate

    return updated, tuple(changed)


def changed_keys(before: SessionConfig, after: SessionConfig) -> tuple[str, ...]:
    """Which settings differ between two configurations."""
    return tuple(
        s.key for s in SETTINGS if s.read(before) != s.read(after)
    )
