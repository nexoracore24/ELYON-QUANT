"""The trading session: everything wired together, driven by ticks.

Until now each engine worked and the wiring lived in a demo script. This is the
object that makes the system a thing you can run rather than a set of parts that
pass their tests.

One session owns one instrument. Ticks go in; candles form; on every *confirmed*
candle the pipeline runs in the order ENG-011 fixed:

    market data → context gate → strategy playbook → entry score
                → risk → OMS → position management

Every stage can stop the flow, and each one records why. A session that took no
trades all day should be able to say, bar by bar, what it was waiting for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Sequence

from elyon.modules.execution.domain import (
    BrokerAdapter,
    Clock,
    ManualClock,
    Oms,
    OrderRequest,
    PaperBroker,
    Side,
    client_order_id,
)
from elyon.modules.market_context.domain import (
    MarketContext,
    MarketDna,
    profile_for,
    read_context,
)
from elyon.modules.market_data.domain import (
    AtrProvider,
    BuilderConfig,
    CandleBuilder,
    Tick,
    Timeframe,
)
from elyon.modules.market_data.domain.model import Candle
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.risk.domain import (
    Dimension,
    RiskBudget,
    SizingRequest,
    size_position,
)
from elyon.modules.smart_money.domain.structure import Direction
from elyon.modules.strategy.domain import (
    GateResult,
    PlaybookVerdict,
    build_context,
    evaluate,
    score_setup,
)
from elyon.modules.trading.domain import DecisionRecord, Provenance, explain
from elyon.modules.trading.domain.position import (
    ManagedPosition,
    ManagementAction,
    ManagementDecision,
    manage,
    open_position,
)
from elyon.shared_kernel.edcs.numeric import ZERO, dec

from .config import Mode, SessionConfig


@dataclass(frozen=True, slots=True)
class BarOutcome:
    """What the session did on one confirmed candle, and why it stopped there.

    ``stopped_at`` is the point of this object. "No trade" is not one answer, it
    is eight different ones, and a session that cannot distinguish them cannot
    be debugged.
    """

    index: int
    close_time_ns: int
    stopped_at: str
    reason: str
    context: MarketContext | None = None
    verdict: PlaybookVerdict | None = None
    score: int | None = None
    decision: DecisionRecord | None = None
    order_id: str | None = None
    management: ManagementDecision | None = None

    @property
    def traded(self) -> bool:
        return self.order_id is not None

    def __str__(self) -> str:
        return f"bar {self.index}: [{self.stopped_at}] {self.reason}"


@dataclass(slots=True)
class TradingSession:
    """One instrument, one configuration, one event loop."""

    config: SessionConfig
    dna: MarketDna | None = None
    broker: BrokerAdapter | None = None
    clock: Clock | None = None

    _builder: CandleBuilder = field(init=False)
    _atr: AtrProvider = field(init=False)
    _candles: list[Candle] = field(default_factory=list)
    _oms: Oms = field(init=False)
    _budget: RiskBudget = field(init=False)
    _position: ManagedPosition | None = None
    _last_context: MarketContext | None = None
    _reservation_id: str | None = None
    _committed_risk: Decimal = ZERO
    outcomes: list[BarOutcome] = field(default_factory=list)
    closed_positions: list[ManagedPosition] = field(default_factory=list)

    def __post_init__(self) -> None:
        timeframe = Timeframe(self.config.timeframe)
        self._builder = CandleBuilder(
            self.config.symbol,
            BuilderConfig(timeframe=timeframe, max_lateness_ns=0),
        )
        self._atr = AtrProvider(period=self.config.atr_period, output_scale=8)
        if self.dna is None:
            self.dna = profile_for(self.config.symbol)
        if self.clock is None:
            self.clock = ManualClock()
        if self.broker is None:
            self.broker = PaperBroker(self.clock)
        self._oms = Oms(self.broker, self.clock)
        self._budget = RiskBudget(
            f"{self.config.symbol}-session",
            {
                Dimension.DAILY_LOSS: self.config.risk.daily_loss_amount,
                Dimension.TOTAL_OPEN_RISK: self.config.risk.open_risk_amount,
            },
        )

    # -- driving ----------------------------------------------------------

    def on_tick(self, tick: Tick) -> list[BarOutcome]:
        """Feed one tick. Returns an outcome per candle it confirmed.

        Decisions are taken on confirmed candles only. Acting on a forming bar
        is how a system trades a level that unprints two seconds later.
        """
        produced: list[BarOutcome] = []
        for candle in self._builder.on_tick(tick).confirmed:
            produced.append(self._on_candle(candle))
        return produced

    def feed(self, ticks: Iterable[Tick]) -> list[BarOutcome]:
        produced: list[BarOutcome] = []
        for tick in ticks:
            produced.extend(self.on_tick(tick))
        return produced

    def flush(self) -> list[BarOutcome]:
        """Confirm whatever is still forming. For end-of-data, not for live."""
        return [self._on_candle(c) for c in self._builder.flush()]

    def _on_candle(self, candle: Candle) -> BarOutcome:
        self._candles.append(candle)
        self._atr.update(candle)
        if isinstance(self.clock, ManualClock):
            self.clock.at = candle.close_time_ns

        index = len(self._candles) - 1
        outcome = self._evaluate(candle, index)
        self.outcomes.append(outcome)
        return outcome

    # -- the pipeline -----------------------------------------------------

    def _evaluate(self, candle: Candle, index: int) -> BarOutcome:
        def stop(stage: str, reason: str, **extra) -> BarOutcome:
            return BarOutcome(
                index=index, close_time_ns=candle.close_time_ns,
                stopped_at=stage, reason=reason, **extra,
            )

        # 0. An open position is managed before anything new is considered.
        #    Looking for entries while holding one is how a session ends up
        #    with more risk on than it decided to take.
        if self._position is not None:
            decision = self._manage(candle)
            return stop(
                "management", str(decision), management=decision,
            )

        if index < self.config.warmup_bars:
            return stop(
                "warmup",
                f"{index + 1}/{self.config.warmup_bars} bars accumulated",
            )

        atr = self._atr.value
        if atr is None or atr == ZERO:
            return stop("warmup", "ATR not seeded yet")

        series = CandleSeries.of(self._candles).window(
            index, self.config.lookback_bars
        )

        # 1. Context gate.
        if not self.config.skip_context_gate:
            context = read_context(
                series, atr, self.dna,
                config=self.config.context,
                previous=self._last_context,
            )
            self._last_context = context
            if not context.should_scan:
                return stop("context", context.gate_reason, context=context)
        else:
            context = None

        # 2. Strategy playbook.
        strategy_context = build_context(
            series, atr, symbol=self.config.symbol,
            swing_grade=self.config.swing_grade,
        )
        verdict = evaluate(
            strategy_context, self.config.registry(), config=self.config.playbook()
        )
        if not verdict.tradeable or verdict.direction is None:
            return stop("playbook", verdict.reason, context=context, verdict=verdict)

        # 3. Entry score.
        setup = strategy_context.setup
        score = score_setup(setup, threshold=self.config.entry_score_threshold)
        if not score.tradeable:
            return stop(
                "score", f"{score.total}/100 -- {score.primary_reason}",
                context=context, verdict=verdict, score=score.total,
            )

        # 4. Risk.
        entry = candle.close
        stop_price = setup.stop_loss(
            atr * self.dna.sensitivity("stop_buffer_atr")
        )
        target = setup.target
        if stop_price is None or target is None:
            return stop(
                "risk", "no invalidation or no target: the trade has no geometry",
                context=context, verdict=verdict, score=score.total,
            )

        # The geometry comes from the six-pillar setup while the side comes from
        # the playbook, and those two can disagree. When they do, the stop is
        # computed for one direction and the order placed in the other -- a long
        # with its stop above entry, which is a guaranteed loss rather than a
        # trade. An internal disagreement is a reason to stand down, not
        # something to correct silently.
        long = verdict.direction is Direction.UP
        coherent = (
            stop_price < entry < target if long else target < entry < stop_price
        )
        if not coherent:
            return stop(
                "risk",
                f"incoherent geometry for a {verdict.direction.name} trade: "
                f"stop {stop_price}, entry {entry}, target {target}. The "
                f"playbook and the setup disagree about direction",
                context=context, verdict=verdict, score=score.total,
            )

        sizing = size_position(
            SizingRequest(
                equity=self.config.risk.equity,
                risk_fraction=self.config.risk.risk_per_trade,
                entry=entry, stop_loss=stop_price, take_profit=target,
                spec=self.config.instrument,
            ),
            min_reward_risk=self.config.risk.min_reward_risk,
        )
        if not sizing.approved:
            return stop(
                "risk",
                f"risk:{sizing.rejection.value.lower() if sizing.rejection else 'refused'}",
                context=context, verdict=verdict, score=score.total,
            )

        reservation = self._budget.reserve(
            intent_id=f"{self.config.symbol}-{index}",
            amounts={
                Dimension.DAILY_LOSS: sizing.risk_amount,
                Dimension.TOTAL_OPEN_RISK: sizing.risk_amount,
            },
            now_ns=candle.close_time_ns,
        )
        if not reservation.granted:
            return stop(
                "risk",
                f"budget refused: {reservation.reason.value if reservation.reason else 'no headroom'}",
                context=context, verdict=verdict, score=score.total,
            )

        # 5. Decision record -- written whether or not it becomes an order.
        record = DecisionRecord(
            symbol=self.config.symbol,
            bar_close_time_ns=candle.close_time_ns,
            side="LONG" if verdict.direction is Direction.UP else "SHORT",
            action="enter_long" if verdict.direction is Direction.UP else "enter_short",
            score=score,
            provenance=Provenance(
                data_version=f"{self.config.symbol}:{candle.data_hash[:12]}",
                config_hash=self.config.config_hash,
                dna_hash=self.dna.dna_hash,
            ),
            detected={
                "pillars": f"{setup.pillars_found}/6",
                "atr": str(atr),
                "context": str(context.score) if context else "skipped",
            },
        )

        # 6. Execution.
        order_id = self._place(record, verdict.direction, sizing.lots,
                               stop_price, target)

        # The reservation becomes committed risk only once the order is live.
        # Committing on intent would hold budget against orders that never
        # reach a broker.
        assert reservation.reservation is not None
        self._budget.commit(reservation.reservation.reservation_id)
        self._reservation_id = reservation.reservation.reservation_id
        self._committed_risk = sizing.risk_amount

        self._position = open_position(
            symbol=self.config.symbol,
            direction=verdict.direction,
            entry=entry,
            stop=stop_price,
            target=target,
            quantity=sizing.lots,
            at_index=index,
        )

        return BarOutcome(
            index=index, close_time_ns=candle.close_time_ns,
            stopped_at="executed",
            reason=f"{verdict.direction.name} {sizing.lots} @ {entry}, "
                   f"stop {stop_price}, target {target}",
            context=context, verdict=verdict, score=score.total,
            decision=record, order_id=order_id,
        )

    def _place(
        self,
        record: DecisionRecord,
        direction: Direction,
        quantity: Decimal,
        stop: Decimal,
        target: Decimal,
    ) -> str:
        coid = client_order_id(record.decision_id)
        request = OrderRequest(
            client_order_id=coid,
            correlation_id=record.decision_id,
            symbol=self.config.symbol,
            side=Side.BUY if direction is Direction.UP else Side.SELL,
            quantity=quantity,
            stop_loss=stop,
            take_profit=target,
        )
        self._oms.create(request)
        self._oms.validate(coid)
        self._oms.approve_risk(coid, reason=f"sized {quantity}")
        self._oms.queue(coid)
        self._oms.send(coid)
        return coid

    def _manage(self, candle: Candle) -> ManagementDecision:
        assert self._position is not None
        atr = self._atr.value or self.dna.typical_atr
        decision = manage(
            self._position, candle, atr, policy=self.config.management
        )
        self._position = decision.position

        if decision.action is ManagementAction.CLOSE:
            self.closed_positions.append(decision.position)
            self._position = None
            # Open risk goes back to the budget; the day's loss allowance does
            # not. A closed loser has spent that allowance for good, and
            # handing it back would let one bad day run forever.
            self._budget.release_committed(
                {Dimension.TOTAL_OPEN_RISK: self._committed_risk}
            )
            self._committed_risk = ZERO
            self._reservation_id = None
        return decision

    # -- reporting --------------------------------------------------------

    @property
    def position(self) -> ManagedPosition | None:
        return self._position

    @property
    def oms(self) -> Oms:
        return self._oms

    @property
    def realized_r(self) -> Decimal:
        return sum((p.realized_r for p in self.closed_positions), ZERO)

    def stopped_at_counts(self) -> dict[str, int]:
        """Where the pipeline stopped, and how often.

        The most useful number in the system when a session is not trading:
        it says which stage to look at instead of guessing.
        """
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.stopped_at] = counts.get(outcome.stopped_at, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def summary(self) -> str:
        counts = self.stopped_at_counts()
        trades = [o for o in self.outcomes if o.traded]
        lines = [
            f"{self.config.symbol} · {self.config.mode.value} · "
            f"{len(self.outcomes)} bars",
            f"  entries taken   {len(trades)}",
            f"  positions closed {len(self.closed_positions)}",
            f"  realized        {self.realized_r}R",
            "  where the pipeline stopped:",
        ]
        for stage, count in counts.items():
            lines.append(f"    {stage:<14} {count:>5}")
        return "\n".join(lines)
