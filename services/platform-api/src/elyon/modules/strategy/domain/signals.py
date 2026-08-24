"""What a strategy is given, and what it hands back.

Every strategy reads the same context -- built once per bar and shared -- so
thirteen strategies do not recompute swings thirteen times, and more importantly
so they cannot quietly disagree about what the market did. One reading of the
bars, many interpretations of it.

Every strategy returns a signal even when it has nothing to say. An abstention
carries its reason exactly like a firing does; a strategy that answers silence
is indistinguishable from one that crashed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from elyon.modules.market_data.domain.model import Candle
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.smart_money.domain.liquidity import (
    LiquidityPool,
    Sweep,
    build_pools,
    detect_sweeps,
)
from elyon.modules.smart_money.domain.structure import (
    Direction,
    Displacement,
    Structure,
    Swing,
    build_structure,
    detect_swings,
)
from elyon.shared_kernel.edcs.numeric import ONE, ZERO, dec, quantize_ratio

from .catalog import StrategyId
from .sessions import Killzone, SessionClock
from .six_pillars import SixPillarSetup, locate_six_pillars


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """One reading of the market, shared by every strategy on the bar."""

    series: CandleSeries
    atr: Decimal
    symbol: str
    swings: tuple[Swing, ...]
    structure: Structure
    pools: tuple[LiquidityPool, ...]
    sweeps: tuple[Sweep, ...]
    setup: SixPillarSetup
    clock: SessionClock

    @property
    def index(self) -> int:
        return len(self.series) - 1

    @property
    def candle(self) -> Candle:
        return self.series[-1]

    @property
    def price(self) -> Decimal:
        return self.series[-1].close

    @property
    def displacement(self) -> Displacement | None:
        return self.setup.displacement

    @property
    def killzone(self) -> Killzone:
        return self.clock.killzone(self.candle.close_time_ns)

    def in_killzone(self, *zones: Killzone) -> bool:
        return self.clock.in_killzone(self.candle.close_time_ns, *zones)

    def bars_of_local_day(self, date: str | None = None) -> tuple[int, ...]:
        """Indices belonging to one local calendar day.

        Session models need "today's range", and a day boundary in New York is
        not a day boundary in UTC. Getting that wrong shifts every session
        model by several hours.
        """
        target = date or self.clock.local_date(self.candle.close_time_ns)
        return tuple(
            i for i in range(len(self.series))
            if self.clock.local_date(self.series[i].close_time_ns) == target
        )


def build_context(
    series: CandleSeries,
    atr: Decimal,
    *,
    symbol: str = "",
    swing_grade: int = 1,
    clock: SessionClock | None = None,
) -> StrategyContext:
    """Compute the shared reading once."""
    swings = detect_swings(series, grade=swing_grade)
    pools = build_pools(swings, atr)
    sweeps: list[Sweep] = []
    for i in range(len(series)):
        sweeps.extend(detect_sweeps(series, pools, i, atr))

    return StrategyContext(
        series=series,
        atr=atr,
        symbol=symbol,
        swings=tuple(swings),
        structure=build_structure(series, grade=swing_grade),
        pools=tuple(pools),
        sweeps=tuple(sweeps),
        setup=locate_six_pillars(series, atr, symbol=symbol, swing_grade=swing_grade),
        clock=clock or SessionClock(),
    )


@dataclass(frozen=True, slots=True)
class StrategySignal:
    """One strategy's verdict on one bar.

    ``direction is None`` means abstain, and an abstention still carries its
    reason. Confidence is the strategy's own read of its setup quality on a 0-1
    scale; it is deliberately *not* a probability, and the playbook scales it by
    the tier before it counts for anything.
    """

    strategy: StrategyId
    direction: Direction | None
    confidence: Decimal
    reason: str
    evidence: tuple[str, ...] = ()
    entry_zone: tuple[Decimal, Decimal] | None = None
    invalidation: Decimal | None = None
    target: Decimal | None = None

    def __post_init__(self) -> None:
        if not ZERO <= self.confidence <= ONE:
            raise ValueError(
                f"confidence {self.confidence} outside [0, 1] for "
                f"{self.strategy.value}"
            )
        if self.direction is None and self.confidence != ZERO:
            raise ValueError("an abstaining strategy cannot carry confidence")

    @property
    def fired(self) -> bool:
        return self.direction is not None

    def __str__(self) -> str:
        if not self.fired:
            return f"· {self.strategy.value}: {self.reason}"
        assert self.direction is not None
        return (
            f"✓ {self.strategy.value} {self.direction.name} "
            f"@{self.confidence}: {self.reason}"
        )


def abstain(strategy: StrategyId, reason: str) -> StrategySignal:
    """The common case, spelled once."""
    return StrategySignal(strategy, None, ZERO, reason)


def fire(
    strategy: StrategyId,
    direction: Direction,
    confidence: str | Decimal,
    reason: str,
    *,
    evidence: tuple[str, ...] = (),
    entry_zone: tuple[Decimal, Decimal] | None = None,
    invalidation: Decimal | None = None,
    target: Decimal | None = None,
) -> StrategySignal:
    # Quantized at the boundary: confidence is a dimensionless ratio, and
    # letting full-precision division escape here would put 28 digits of noise
    # into every explanation and hash.
    raw = dec(confidence) if isinstance(confidence, str) else confidence
    return StrategySignal(
        strategy=strategy,
        direction=direction,
        confidence=quantize_ratio(raw),
        reason=reason,
        evidence=evidence,
        entry_zone=entry_zone,
        invalidation=invalidation,
        target=target,
    )


class StrategyEvaluator(Protocol):
    """The shape every play implements."""

    def __call__(self, context: StrategyContext) -> StrategySignal: ...
