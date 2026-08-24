"""Walk-forward simulation.

Every backtest is a claim about what a system *would have* done, and there are
exactly three ways that claim usually turns out to be a lie. This module is
built around refusing all three.

**Look-ahead.** The simulator never hands a strategy anything but
``series.window(i, lookback)`` -- the bars up to and including ``i``, and no
others. The guarantee is structural rather than a matter of discipline: a play
physically cannot see bar ``i+1``, because it is not in the object it was given.
The window is bounded at the far end too, which keeps the run linear in the
number of bars and matches what a trader actually looks at.

**Intrabar optimism.** When one bar's range contains both the stop and the
target, the order they were touched in is unknowable from OHLC. The honest
answer is that you do not know, and the only safe assumption is the bad one:
the stop went first. A simulator that resolves those bars in its own favour
will report a beautiful equity curve for a system that loses money.

**Costless fills.** Spread and slippage are applied to every fill, always
against the trade. Expectancy without costs is measuring a different system
than the one that would trade.

There is a fourth lie the simulator cannot prevent, only label: measuring a
strategy on the data it was designed on. See ``Sample`` in ``report``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterator

from elyon.modules.market_data.domain.atr import AtrProvider
from elyon.modules.market_data.domain.model import Candle
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.smart_money.domain.structure import Direction
from elyon.modules.strategy.domain import (
    Calibration,
    PlaybookConfig,
    ProbabilityTier,
    SessionClock,
    StrategyId,
    StrategyRegistry,
    build_context,
    evaluate,
)
from elyon.shared_kernel.edcs.canonical import config_hash
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec

from .costs import DEFAULT_COSTS, CostModel
from .trade import ExitReason, FillModel, SimulatedTrade, TradeIntent


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """How the simulation is run.

    Every field here changes the result, so all of them hash into the run's
    provenance. A backtest whose settings are not recorded cannot be defended
    later, and "we must have used different settings" is not a defence.
    """

    atr_period: int = 14
    atr_scale: int = 5
    swing_grade: int = 1
    warmup_bars: int = 20
    # How much history a strategy is shown. Bounded on purpose: it keeps the
    # run linear in the number of bars rather than quadratic, and it matches
    # what a trader actually looks at.
    lookback_bars: int = 120
    max_bars_in_trade: int = 60
    # How long a resting entry order stays live before it is abandoned.
    entry_expiry_bars: int = 5
    fill_model: FillModel = FillModel.MARKET_NEXT_OPEN
    costs: CostModel = DEFAULT_COSTS
    # Minimum reward-to-risk to accept an intent at all. Without a floor the
    # sample fills with 0.3R lottery tickets that no one would really take.
    min_reward_risk: Decimal = dec("1.5")
    # And a ceiling, which matters more than it looks. An unbounded R:R is not
    # a bonus, it is a warning: a 20R "target" is a level price will almost
    # never reach, so the trade always exits on time rather than at the target,
    # and whatever the drift happened to be gets booked as the result. One such
    # trade can carry an entire backtest.
    max_reward_risk: Decimal = dec("8")

    def __post_init__(self) -> None:
        if self.warmup_bars < self.atr_period:
            raise DeterminismError(
                f"warmup of {self.warmup_bars} bars is shorter than the ATR "
                f"period of {self.atr_period}; the first signals would be "
                f"computed from an unseeded ATR"
            )
        if self.lookback_bars < self.warmup_bars:
            raise DeterminismError(
                f"lookback of {self.lookback_bars} bars is shorter than the "
                f"warmup of {self.warmup_bars}; strategies would see less "
                f"history than the run waited to accumulate"
            )

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "atrPeriod": self.atr_period,
            "atrScale": self.atr_scale,
            "swingGrade": self.swing_grade,
            "warmupBars": self.warmup_bars,
            "lookbackBars": self.lookback_bars,
            "maxBarsInTrade": self.max_bars_in_trade,
            "entryExpiryBars": self.entry_expiry_bars,
            "fillModel": self.fill_model.value,
            "minRewardRisk": str(self.min_reward_risk),
            "spread": str(self.costs.spread),
            "slippage": str(self.costs.slippage),
            "commission": str(self.costs.commission_per_unit),
        }

    @property
    def config_hash(self) -> str:
        return config_hash(self.to_canonical_dict())


# Calibration needs a strategy to actually take trades, but the live gate
# refuses uncalibrated strategies -- which is the deadlock the whole tier system
# exists inside. Research breaks it explicitly: during a calibration run every
# strategy is treated as proven so it can generate the sample that will decide
# what it really is. This bypass is named, confined to this module, and must
# never be reachable from the live path.
_RESEARCH_PROVISIONAL = Calibration(
    sample_size=10_000, wins=5_000, expectancy_r=dec("1"), dataset="__research__"
)


def research_config(
    strategies: tuple[StrategyId, ...], base: PlaybookConfig | None = None
) -> PlaybookConfig:
    """A playbook config that lets uncalibrated strategies trade, for measurement.

    Using this outside a backtest would hand real money to a strategy on the
    strength of a placeholder, which is precisely what the tier system exists
    to prevent.
    """
    from dataclasses import replace

    settings = base or PlaybookConfig()
    grants = dict(settings.calibrations)
    for strategy in strategies:
        grants.setdefault(strategy, _RESEARCH_PROVISIONAL)
    return replace(settings, calibrations=grants)


@dataclass(slots=True)
class _OpenTrade:
    intent: TradeIntent
    entry_index: int
    fill: Decimal

    @property
    def risk(self) -> Decimal:
        return abs(self.fill - self.intent.stop)


def _atr_series(series: CandleSeries, config: SimulationConfig) -> list[Decimal | None]:
    """ATR as of each bar's close, computed once forward.

    Recomputing per prefix would be O(n^2) and, worse, would let a subtle bug
    give later bars a different ATR history than earlier ones saw.
    """
    provider = AtrProvider(period=config.atr_period, output_scale=config.atr_scale)
    values: list[Decimal | None] = []
    for candle in series:
        provider.update(candle)
        values.append(provider.value)
    return values


def _resolve_bar(
    trade: _OpenTrade, candle: Candle
) -> tuple[Decimal, ExitReason] | None:
    """Did this bar close the trade, and at what price?

    The pessimistic rule lives here. When a bar's range covers both levels, OHLC
    cannot say which came first, so the stop is assumed -- always, and without a
    tunable to turn it off, because that tunable is how backtests start lying.
    """
    long = trade.intent.direction is Direction.UP
    stop, target = trade.intent.stop, trade.intent.target

    # A gap through the stop fills at the open, which is worse than the stop.
    if long and candle.open <= stop:
        return candle.open, ExitReason.GAP_THROUGH_STOP
    if not long and candle.open >= stop:
        return candle.open, ExitReason.GAP_THROUGH_STOP

    hit_stop = candle.low <= stop if long else candle.high >= stop
    hit_target = candle.high >= target if long else candle.low <= target

    if hit_stop:
        return stop, ExitReason.STOP      # wins the tie, deliberately
    if hit_target:
        return target, ExitReason.TARGET
    return None


def _intent_from(
    verdict, context, index: int, config: SimulationConfig
) -> TradeIntent | None:
    """Turn a tradeable verdict into a concrete order, or explain nothing doing."""
    if not verdict.tradeable or verdict.direction is None:
        return None

    setup = context.setup
    price = context.price
    atr = context.atr
    stop = setup.stop_loss(atr * dec("0.3"))
    target = setup.target

    if stop is None or target is None:
        return None

    # Both have to be on the correct side of the entry. When a target sits on
    # the wrong side, the setup is describing a move that has already happened.
    long = verdict.direction is Direction.UP
    if long and not (stop < price < target):
        return None
    if not long and not (target < price < stop):
        return None

    risk = abs(price - stop)
    if risk == ZERO:
        return None
    reward_risk = abs(target - price) / risk
    if not config.min_reward_risk <= reward_risk <= config.max_reward_risk:
        return None

    strategy = verdict.fired[0].strategy if verdict.fired else StrategyId.SIX_PILLARS
    return TradeIntent(
        strategy=strategy,
        direction=verdict.direction,
        signal_index=index,
        entry=price,
        stop=stop,
        target=target,
        entry_zone=setup.entry_zone,
        reason=verdict.reason,
    )


def simulate(
    series: CandleSeries,
    registry: StrategyRegistry,
    *,
    symbol: str = "",
    config: SimulationConfig | None = None,
    playbook: PlaybookConfig | None = None,
    clock: SessionClock | None = None,
) -> list[SimulatedTrade]:
    """Walk the series bar by bar and record what would have happened.

    One position at a time. Running concurrent positions would need a portfolio
    model and a risk budget shared across them, and pretending otherwise makes
    the R figures incomparable.
    """
    settings = config or SimulationConfig()
    atrs = _atr_series(series, settings)

    trades: list[SimulatedTrade] = []
    open_trade: _OpenTrade | None = None
    pending: tuple[TradeIntent, int] | None = None

    for i in range(settings.warmup_bars, len(series)):
        candle = series[i]

        # 1. An open trade is resolved before anything new is considered.
        if open_trade is not None:
            outcome = _resolve_bar(open_trade, candle)
            if outcome is not None:
                ideal, reason = outcome
                trades.append(_close(open_trade, i, ideal, reason, settings))
                open_trade = None
            elif i - open_trade.entry_index >= settings.max_bars_in_trade:
                trades.append(
                    _close(open_trade, i, candle.close, ExitReason.EXPIRED, settings)
                )
                open_trade = None
            else:
                continue  # still in the trade; take no new signal

        # 2. A resting order may fill on this bar.
        if pending is not None and open_trade is None:
            intent, placed_at = pending
            fill = _try_fill(intent, candle, settings)
            if fill is not None:
                open_trade = _OpenTrade(intent, i, fill)
                pending = None
                continue
            if i - placed_at >= settings.entry_expiry_bars:
                pending = None

        if open_trade is not None or pending is not None:
            continue

        # 3. Look for a new signal, seeing only what had happened by now.
        atr = atrs[i]
        if atr is None or atr == ZERO:
            continue

        # A bounded window, not the whole history. Reading every bar ever
        # recorded is both quadratic and wrong: a swing from eight months ago
        # is not structure today's market is trading against.
        window = series.window(i, settings.lookback_bars)
        context = build_context(
            window, atr, symbol=symbol,
            swing_grade=settings.swing_grade, clock=clock,
        )
        verdict = evaluate(context, registry, config=playbook)
        intent = _intent_from(verdict, context, i, settings)
        if intent is not None:
            pending = (intent, i)

    # A trade still open when the data ends is reported, not discarded: hiding
    # it would bias the sample toward trades that resolved.
    if open_trade is not None:
        last = len(series) - 1
        trades.append(
            _close(open_trade, last, series[last].close,
                   ExitReason.END_OF_DATA, settings)
        )

    return trades


def _try_fill(
    intent: TradeIntent, candle: Candle, config: SimulationConfig
) -> Decimal | None:
    """Would this bar have filled the entry?"""
    if config.fill_model is FillModel.MARKET_NEXT_OPEN:
        return config.costs.entry_price(candle.open, intent.direction)

    zone = intent.entry_zone
    if zone is None:
        return config.costs.entry_price(candle.open, intent.direction)

    low, high = zone
    if candle.low > high or candle.high < low:
        return None

    # Fill at the zone edge price reaches first, not at the best price in the
    # zone: assuming the best fill is free money the backtest invents.
    touched = high if intent.direction is Direction.UP else low
    return config.costs.entry_price(touched, intent.direction)


def _close(
    trade: _OpenTrade,
    index: int,
    ideal_exit: Decimal,
    reason: ExitReason,
    config: SimulationConfig,
) -> SimulatedTrade:
    return SimulatedTrade(
        strategy=trade.intent.strategy,
        direction=trade.intent.direction,
        signal_index=trade.intent.signal_index,
        entry_index=trade.entry_index,
        exit_index=index,
        entry=trade.fill,
        stop=trade.intent.stop,
        target=trade.intent.target,
        exit_price=config.costs.exit_price(ideal_exit, trade.intent.direction),
        reason=reason,
        risk=trade.risk,
    )
