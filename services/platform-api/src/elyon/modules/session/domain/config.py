"""What a running instance is configured to do.

One object, loadable from JSON, hashable into every decision. Two properties it
has to hold:

*   **Nothing is implicit.** If a setting affects a trade, it is here and it is
    in the hash. "We must have been running different settings" is not an
    explanation anybody can check six months later.
*   **Nothing dangerous is a default.** Live trading, disabled context gates and
    uncalibrated strategies going live all have to be asked for by name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from elyon.modules.execution.domain import Side
from elyon.modules.market_context.domain import ContextConfig
from elyon.modules.risk.domain import InstrumentSpec
from elyon.modules.strategy.domain import (
    Calibration,
    ConflictPolicy,
    PlaybookConfig,
    StrategyId,
    StrategyRegistry,
)
from elyon.modules.trading.domain.position import ManagementPolicy
from elyon.shared_kernel.edcs.canonical import config_hash
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec


class Mode(str, Enum):
    """Which reality this instance is operating in.

    PAPER is the default and LIVE has to be typed out. A system where the
    dangerous mode is the one you get by forgetting to choose is a system that
    will eventually trade real money by accident.
    """

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"

    @property
    def touches_real_money(self) -> bool:
        return self is Mode.LIVE


@dataclass(frozen=True, slots=True)
class RiskSettings:
    equity: Decimal = dec("10000")
    risk_per_trade: Decimal = dec("0.005")      # 0.5%
    daily_loss_limit: Decimal = dec("0.02")     # 2% of equity
    max_open_risk: Decimal = dec("0.02")
    min_reward_risk: Decimal = dec("1.5")
    max_concurrent_positions: int = 1

    def __post_init__(self) -> None:
        if not ZERO < self.risk_per_trade <= dec("0.05"):
            raise DeterminismError(
                f"risk per trade of {self.risk_per_trade} is outside (0, 0.05]. "
                f"Above 5% per trade, a normal losing streak is an account "
                f"blow-up rather than a drawdown."
            )
        if self.daily_loss_limit < self.risk_per_trade:
            raise DeterminismError(
                f"daily loss limit {self.daily_loss_limit} is below the risk of "
                f"a single trade ({self.risk_per_trade}); the first loss would "
                f"halt the day"
            )
        if self.equity <= ZERO:
            raise DeterminismError("equity must be positive")

    @property
    def daily_loss_amount(self) -> Decimal:
        return self.equity * self.daily_loss_limit

    @property
    def open_risk_amount(self) -> Decimal:
        return self.equity * self.max_open_risk


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Everything one running instance needs."""

    symbol: str
    mode: Mode = Mode.PAPER
    timeframe: str = "M1"
    strategies: tuple[StrategyId, ...] = (StrategyId.SIX_PILLARS,)
    shadow_strategies: tuple[StrategyId, ...] = ()
    conflict_policy: ConflictPolicy = ConflictPolicy.VETO
    calibrations: Mapping[StrategyId, Calibration] = field(default_factory=dict)

    risk: RiskSettings = field(default_factory=RiskSettings)
    management: ManagementPolicy = field(default_factory=ManagementPolicy)
    context: ContextConfig = field(default_factory=ContextConfig)
    instrument: InstrumentSpec = field(
        default_factory=lambda: InstrumentSpec(
            lot_step=dec("0.01"), min_lot=dec("0.01"),
            max_lot=dec("100"), value_per_price_unit=dec("100000"),
        )
    )

    atr_period: int = 14
    swing_grade: int = 1
    warmup_bars: int = 40
    lookback_bars: int = 120
    entry_score_threshold: int | None = None

    # Deliberate escape hatches, each of which has to be asked for by name.
    allow_uncalibrated_live: bool = False
    skip_context_gate: bool = False

    def __post_init__(self) -> None:
        if self.warmup_bars < self.atr_period:
            raise DeterminismError(
                f"warmup of {self.warmup_bars} bars is shorter than the ATR "
                f"period of {self.atr_period}"
            )
        if self.lookback_bars < self.warmup_bars:
            raise DeterminismError(
                f"lookback {self.lookback_bars} is shorter than warmup "
                f"{self.warmup_bars}"
            )
        if not self.strategies:
            raise DeterminismError(
                "no strategies enabled; the session would evaluate nothing"
            )
        overlap = set(self.strategies) & set(self.shadow_strategies)
        if overlap:
            raise DeterminismError(
                f"{', '.join(s.value for s in overlap)} listed as both live and "
                f"shadow; a strategy is one or the other"
            )
        if self.mode.touches_real_money and self.skip_context_gate:
            raise DeterminismError(
                "the context gate cannot be skipped in LIVE mode. It is what "
                "stops the engine looking for entries in markets it should not "
                "be in; disabling it is a research affordance, not a setting."
            )

    # -- derived objects --------------------------------------------------

    def registry(self) -> StrategyRegistry:
        return (
            StrategyRegistry.all_off()
            .live(*self.strategies)
            .shadow(*self.shadow_strategies)
        )

    def playbook(self) -> PlaybookConfig:
        return PlaybookConfig(
            conflict_policy=self.conflict_policy,
            calibrations=dict(self.calibrations),
        )

    def uncalibrated_live(self) -> tuple[StrategyId, ...]:
        """Live strategies with no evidence behind them."""
        playbook = self.playbook()
        from elyon.modules.strategy.domain import ProbabilityTier

        return tuple(
            s for s in self.strategies
            if playbook.tier_of(s) is ProbabilityTier.UNPROVEN
        )

    def warnings(self) -> tuple[str, ...]:
        """What a person should know before letting this run.

        Returned rather than logged: a warning nobody is handed is a warning
        nobody reads.
        """
        notes: list[str] = []
        unproven = self.uncalibrated_live()
        if unproven:
            notes.append(
                f"{len(unproven)} live strategy(ies) have no calibration "
                f"({', '.join(s.value for s in unproven)}); they cannot open a "
                f"trade alone, so this session will take no trades until they "
                f"are measured or a proven strategy is enabled"
            )
        if self.skip_context_gate:
            notes.append(
                "the context gate is disabled; the engine will look for entries "
                "in any market condition"
            )
        if self.mode.touches_real_money:
            notes.append("LIVE mode: orders will reach a real broker")
        return tuple(notes)

    # -- provenance -------------------------------------------------------

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "mode": self.mode.value,
            "timeframe": self.timeframe,
            "strategies": sorted(s.value for s in self.strategies),
            "shadowStrategies": sorted(s.value for s in self.shadow_strategies),
            "conflictPolicy": self.conflict_policy.value,
            "calibrated": sorted(s.value for s in self.calibrations),
            "riskPerTrade": str(self.risk.risk_per_trade),
            "dailyLossLimit": str(self.risk.daily_loss_limit),
            "minRewardRisk": str(self.risk.min_reward_risk),
            "maxConcurrentPositions": self.risk.max_concurrent_positions,
            "atrPeriod": self.atr_period,
            "swingGrade": self.swing_grade,
            "warmupBars": self.warmup_bars,
            "lookbackBars": self.lookback_bars,
            "breakEvenAtR": str(self.management.break_even_at_r),
            "trailFromR": str(self.management.trail_from_r),
            "partialAtR": str(self.management.partial_at_r),
            "skipContextGate": self.skip_context_gate,
        }

    @property
    def config_hash(self) -> str:
        return config_hash(self.to_canonical_dict())

    # -- serialisation ----------------------------------------------------

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SessionConfig":
        """Build from plain data, failing loudly on anything unrecognised.

        Unknown keys are an error rather than being ignored: a typo in a config
        file that silently leaves a setting at its default is a bug that shows
        up as unexplained behaviour weeks later.
        """
        known = {
            "symbol", "mode", "timeframe", "strategies", "shadowStrategies",
            "conflictPolicy", "risk", "atrPeriod", "swingGrade", "warmupBars",
            "lookbackBars", "entryScoreThreshold", "allowUncalibratedLive",
            "skipContextGate", "management",
        }
        unknown = set(raw) - known
        if unknown:
            raise DeterminismError(
                f"unknown configuration key(s): {', '.join(sorted(unknown))}. "
                f"Known keys: {', '.join(sorted(known))}"
            )

        if "symbol" not in raw:
            raise DeterminismError("configuration must name a symbol")

        risk_raw = raw.get("risk", {})
        risk = RiskSettings(
            equity=dec(str(risk_raw.get("equity", "10000"))),
            risk_per_trade=dec(str(risk_raw.get("riskPerTrade", "0.005"))),
            daily_loss_limit=dec(str(risk_raw.get("dailyLossLimit", "0.02"))),
            max_open_risk=dec(str(risk_raw.get("maxOpenRisk", "0.02"))),
            min_reward_risk=dec(str(risk_raw.get("minRewardRisk", "1.5"))),
            max_concurrent_positions=int(
                risk_raw.get("maxConcurrentPositions", 1)
            ),
        )

        management_raw = raw.get("management", {})
        management = ManagementPolicy(
            break_even_at_r=_optional_dec(management_raw.get("breakEvenAtR", "1.0")),
            trail_from_r=_optional_dec(management_raw.get("trailFromR", "1.5")),
            trail_distance_atr=dec(
                str(management_raw.get("trailDistanceAtr", "1.5"))
            ),
            partial_at_r=_optional_dec(management_raw.get("partialAtR", "1.5")),
            partial_fraction=dec(
                str(management_raw.get("partialFraction", "0.5"))
            ),
            time_stop_bars=management_raw.get("timeStopBars", 40),
        )

        return cls(
            symbol=str(raw["symbol"]),
            mode=Mode(raw.get("mode", "PAPER")),
            timeframe=str(raw.get("timeframe", "M1")),
            strategies=_strategy_tuple(raw.get("strategies", ["SIX_PILLARS"])),
            shadow_strategies=_strategy_tuple(raw.get("shadowStrategies", [])),
            conflict_policy=ConflictPolicy(raw.get("conflictPolicy", "VETO")),
            risk=risk,
            management=management,
            atr_period=int(raw.get("atrPeriod", 14)),
            swing_grade=int(raw.get("swingGrade", 1)),
            warmup_bars=int(raw.get("warmupBars", 40)),
            lookback_bars=int(raw.get("lookbackBars", 120)),
            entry_score_threshold=raw.get("entryScoreThreshold"),
            allow_uncalibrated_live=bool(raw.get("allowUncalibratedLive", False)),
            skip_context_gate=bool(raw.get("skipContextGate", False)),
        )

    @classmethod
    def load(cls, path: str | Path) -> "SessionConfig":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_canonical_dict(), indent=2))


def _optional_dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return dec(str(value))


def _strategy_tuple(names: Any) -> tuple[StrategyId, ...]:
    resolved = []
    for name in names:
        try:
            resolved.append(StrategyId(name))
        except ValueError as exc:
            known = ", ".join(s.value for s in StrategyId)
            raise DeterminismError(
                f"unknown strategy {name!r}; known strategies are: {known}"
            ) from exc
    return tuple(resolved)
