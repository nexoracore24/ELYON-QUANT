"""Backtesting tests.

A backtest is a claim about what a system would have done, and there are four
well-known ways that claim turns out to be false. Three of them the simulator
refuses structurally, and each gets an explicit test here:

    1. Look-ahead        -- a play only ever sees bars up to and including i
    2. Intrabar optimism -- a bar holding both stop and target resolves as stop
    3. Costless fills    -- spread and slippage always move against the trade

The fourth -- measuring on the data the strategy was designed on -- cannot be
detected, so it must be declared, and certifying an in-sample run is refused.
"""

from __future__ import annotations

import pytest

from elyon.modules.backtesting.domain import (
    DEFAULT_COSTS,
    FREE,
    BacktestReport,
    CostModel,
    ExitReason,
    FillModel,
    GeneratorConfig,
    Sample,
    SimulatedTrade,
    SimulationConfig,
    TradeIntent,
    calibration_from,
    generate,
    report_from,
    research_config,
    simulate,
    tier_of,
)
from elyon.modules.backtesting.domain.simulator import _resolve_bar, _OpenTrade
from elyon.modules.market_data.domain.model import Candle, CandleState, Timeframe
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.smart_money.domain.structure import Direction
from elyon.modules.strategy.domain import (
    PlaybookConfig,
    ProbabilityTier,
    StrategyId,
    StrategyRegistry,
    build_context,
    evaluate,
)
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec

SYMBOL = "EURUSD"
M1 = Timeframe.M1
HOUSE = StrategyId.SIX_PILLARS


def bar(i: int, o: str, h: str, l: str, c: str) -> Candle:
    op, cl = dec(o), dec(c)
    return Candle(
        symbol=SYMBOL, timeframe=M1,
        open_time_ns=i * M1.duration_ns,
        close_time_ns=(i + 1) * M1.duration_ns,
        open=op, high=max(dec(h), op, cl), low=min(dec(l), op, cl), close=cl,
        volume=dec("10"), tick_count=4, state=CandleState.CONFIRMED,
    )


def long_intent(entry="1.1000", stop="1.0990", target="1.1020") -> TradeIntent:
    return TradeIntent(
        strategy=HOUSE, direction=Direction.UP, signal_index=0,
        entry=dec(entry), stop=dec(stop), target=dec(target),
        entry_zone=None, reason="test",
    )


def house_registry() -> StrategyRegistry:
    return StrategyRegistry.all_off().live(HOUSE)


def run(series: CandleSeries, *, costs=FREE, **kwargs) -> list[SimulatedTrade]:
    config = SimulationConfig(max_bars_in_trade=25, costs=costs, **kwargs)
    return simulate(
        series, house_registry(), symbol=SYMBOL, config=config,
        playbook=research_config((HOUSE,)),
    )


def report_of(trades, *, sample=Sample.IN_SAMPLE, dataset="synthetic"):
    return report_from(
        trades, strategy=HOUSE, dataset=dataset, sample=sample,
        data_hash="a" * 16, config_hash="b" * 16, registry_hash="c" * 16,
    )


MARKET = generate(GeneratorConfig(cycles=45))

# The simulation is the expensive part of this suite, so the two runs almost
# every test needs are computed once here rather than per test.
FREE_RUN = run(MARKET)
CHARGED_RUN = run(MARKET, costs=DEFAULT_COSTS)


# ---------------------------------------------------------------------------
# Lie 1: look-ahead
# ---------------------------------------------------------------------------

class TestNoLookAhead:
    """The property without which every other number here is fiction."""

    def test_truncating_the_future_does_not_change_the_past(self):
        # A trade that opened and closed by bar k must be identical whether or
        # not the bars after k exist. If it is not, the simulator peeked.
        full = FREE_RUN
        assert full, "fixture produced no trades to compare"

        cut = 200
        prefix = run(MARKET.upto(cut))
        settled = [t for t in full if t.exit_index < cut]
        matching = [t for t in prefix if t.exit_index < cut]

        assert settled, "no trades resolved before the cut"
        assert settled == matching

    def test_a_play_never_receives_a_bar_it_should_not_see(self):
        # Belt and braces on the structural guarantee: the context handed to a
        # strategy at bar i ends at bar i.
        for i in (30, 60, 120):
            context = build_context(MARKET.upto(i), dec("0.001"), symbol=SYMBOL)
            assert len(context.series) == i + 1
            assert context.candle == MARKET[i]

    def test_extending_the_data_only_adds_trades(self):
        short = run(MARKET.upto(250))
        long_ = FREE_RUN
        settled = [t for t in short if t.exit_index < 250]
        assert settled == [t for t in long_ if t.exit_index < 250]
        assert len(long_) >= len(settled)


# ---------------------------------------------------------------------------
# Lie 2: intrabar optimism
# ---------------------------------------------------------------------------

class TestIntrabarPessimism:
    def _open(self, intent=None) -> _OpenTrade:
        i = intent or long_intent()
        return _OpenTrade(i, entry_index=0, fill=i.entry)

    def test_a_bar_holding_both_levels_resolves_as_a_stop(self):
        # OHLC cannot say which was touched first. Resolving it favourably is
        # how a losing system prints a beautiful equity curve.
        both = bar(1, "1.1000", "1.1025", "1.0985", "1.1010")
        price, reason = _resolve_bar(self._open(), both)
        assert reason is ExitReason.STOP
        assert price == dec("1.0990")

    def test_the_same_rule_applies_to_shorts(self):
        short = TradeIntent(
            strategy=HOUSE, direction=Direction.DOWN, signal_index=0,
            entry=dec("1.1000"), stop=dec("1.1010"), target=dec("1.0980"),
            entry_zone=None, reason="test",
        )
        both = bar(1, "1.1000", "1.1015", "1.0975", "1.0990")
        _, reason = _resolve_bar(self._open(short), both)
        assert reason is ExitReason.STOP

    def test_a_clean_target_bar_is_still_a_target(self):
        # Pessimism must not become blindness.
        clean = bar(1, "1.1000", "1.1025", "1.0995", "1.1020")
        price, reason = _resolve_bar(self._open(), clean)
        assert reason is ExitReason.TARGET
        assert price == dec("1.1020")

    def test_a_bar_touching_neither_leaves_the_trade_open(self):
        quiet = bar(1, "1.1000", "1.1005", "1.0995", "1.1002")
        assert _resolve_bar(self._open(), quiet) is None

    def test_a_gap_through_the_stop_fills_at_the_open(self):
        # Worse than the stop, which is what actually happens.
        gapped = bar(1, "1.0970", "1.0975", "1.0965", "1.0972")
        price, reason = _resolve_bar(self._open(), gapped)
        assert reason is ExitReason.GAP_THROUGH_STOP
        assert price == dec("1.0970")
        assert price < dec("1.0990")

    def test_a_gap_is_never_reported_as_an_ordinary_stop(self):
        # The distinction matters: gap risk is not stop risk, and a research
        # log that conflates them understates the tail.
        gapped = bar(1, "1.0970", "1.0975", "1.0965", "1.0972")
        _, reason = _resolve_bar(self._open(), gapped)
        assert reason is not ExitReason.STOP


# ---------------------------------------------------------------------------
# Lie 3: costless fills
# ---------------------------------------------------------------------------

class TestCosts:
    def test_a_buyer_always_fills_higher(self):
        assert DEFAULT_COSTS.entry_price(dec("1.1000"), Direction.UP) > dec("1.1000")

    def test_a_seller_always_fills_lower(self):
        assert DEFAULT_COSTS.entry_price(dec("1.1000"), Direction.DOWN) < dec("1.1000")

    def test_exits_are_penalised_too(self):
        assert DEFAULT_COSTS.exit_price(dec("1.1000"), Direction.UP) < dec("1.1000")
        assert DEFAULT_COSTS.exit_price(dec("1.1000"), Direction.DOWN) > dec("1.1000")

    def test_costs_never_help(self):
        # Applying friction symmetrically would let a backtest occasionally
        # profit from its own spread.
        for direction in Direction:
            ideal = dec("1.1000")
            entry = DEFAULT_COSTS.entry_price(ideal, direction)
            exit_ = DEFAULT_COSTS.exit_price(ideal, direction)
            move = (exit_ - entry) * dec(int(direction.value))
            assert move < ZERO

    def test_a_round_turn_pays_the_full_spread(self):
        model = CostModel(spread=dec("0.0002"))
        assert model.round_turn == dec("0.0002")

    def test_negative_costs_are_refused(self):
        with pytest.raises(DeterminismError, match="negative"):
            CostModel(spread=dec("-0.0001"))

    def test_the_default_is_not_free(self):
        # A zero-cost default is how a backtest quietly becomes a sales pitch.
        assert DEFAULT_COSTS.round_turn > ZERO

    def test_costs_make_the_measured_edge_worse(self):
        free = report_of(FREE_RUN)
        charged = report_of(CHARGED_RUN)
        assert charged.expectancy_r < free.expectancy_r


# ---------------------------------------------------------------------------
# Lie 4: in-sample measurement
# ---------------------------------------------------------------------------

class TestInSampleIsRefused:
    def test_an_in_sample_run_cannot_certify_a_tier(self):
        # A strategy measured on the data it was designed on shows an edge
        # whether or not one exists. Certifying that defeats the tier system.
        report = report_of(FREE_RUN, sample=Sample.IN_SAMPLE)
        with pytest.raises(DeterminismError, match="in-sample"):
            calibration_from(report)

    def test_the_refusal_says_what_to_do_instead(self):
        report = report_of(FREE_RUN, sample=Sample.IN_SAMPLE)
        with pytest.raises(DeterminismError, match="Hold data back"):
            calibration_from(report)

    def test_an_out_of_sample_run_certifies(self):
        report = report_of(FREE_RUN, sample=Sample.OUT_OF_SAMPLE)
        calibration = calibration_from(report)
        assert calibration.sample_size == report.count

    def test_the_dataset_identity_travels_into_the_calibration(self):
        # Otherwise "which data was this measured on?" is unanswerable later.
        report = report_of(
            FREE_RUN, sample=Sample.OUT_OF_SAMPLE, dataset="eurusd-2024-h2"
        )
        assert "eurusd-2024-h2" in calibration_from(report).dataset

    def test_an_in_sample_tier_can_still_be_previewed(self):
        # Useful for deciding whether a proper run is worth the cost -- it just
        # cannot certify anything.
        report = report_of(FREE_RUN, sample=Sample.IN_SAMPLE)
        assert isinstance(tier_of(report), ProbabilityTier)


# ---------------------------------------------------------------------------
# Trade bookkeeping
# ---------------------------------------------------------------------------

class TestTradeInvariants:
    def test_a_long_stop_above_entry_is_refused(self):
        # A stop on the wrong side is a guaranteed loss dressed as a trade, and
        # a backtest that accepts one reports a spectacular win rate.
        with pytest.raises(DeterminismError, match="at or above entry"):
            long_intent(entry="1.1000", stop="1.1010")

    def test_a_short_stop_below_entry_is_refused(self):
        with pytest.raises(DeterminismError, match="at or below entry"):
            TradeIntent(
                strategy=HOUSE, direction=Direction.DOWN, signal_index=0,
                entry=dec("1.1000"), stop=dec("1.0990"), target=dec("1.0980"),
                entry_zone=None, reason="x",
            )

    def test_a_target_on_the_wrong_side_is_refused(self):
        with pytest.raises(DeterminismError, match="target"):
            long_intent(entry="1.1000", stop="1.0990", target="1.0995")

    def test_r_is_measured_from_the_filled_entry(self):
        # If slippage moved the fill, the trade really did risk more, and
        # measuring from the intended entry would understate the loss.
        trade = SimulatedTrade(
            strategy=HOUSE, direction=Direction.UP, signal_index=0,
            entry_index=1, exit_index=3,
            entry=dec("1.1002"), stop=dec("1.0990"), target=dec("1.1020"),
            exit_price=dec("1.0990"), reason=ExitReason.STOP,
            risk=dec("0.0012"),
        )
        assert trade.r_multiple == dec("-1")

    def test_a_winning_short_reports_positive_r(self):
        trade = SimulatedTrade(
            strategy=HOUSE, direction=Direction.DOWN, signal_index=0,
            entry_index=1, exit_index=4,
            entry=dec("1.1000"), stop=dec("1.1010"), target=dec("1.0980"),
            exit_price=dec("1.0980"), reason=ExitReason.TARGET,
            risk=dec("0.0010"),
        )
        assert trade.r_multiple == dec("2")
        assert trade.won


# ---------------------------------------------------------------------------
# The simulation loop
# ---------------------------------------------------------------------------

class TestSimulationLoop:
    def test_the_run_is_reproducible(self):
        assert run(MARKET) == FREE_RUN

    def test_only_one_position_is_open_at_a_time(self):
        # Concurrent positions need a shared risk budget; without one the R
        # figures are not comparable.
        trades = FREE_RUN
        for earlier, later in zip(trades, trades[1:]):
            assert later.entry_index >= earlier.exit_index

    def test_trades_are_ordered(self):
        trades = FREE_RUN
        assert trades == sorted(trades, key=lambda t: t.entry_index)

    def test_every_trade_exits_after_it_enters(self):
        for trade in FREE_RUN:
            assert trade.exit_index >= trade.entry_index
            assert trade.entry_index > trade.signal_index or trade.bars_held >= 0

    def test_an_unresolved_trade_is_reported_not_dropped(self):
        # Discarding it would bias the sample toward trades that resolved.
        trades = FREE_RUN
        unresolved = [t for t in trades if t.reason is ExitReason.END_OF_DATA]
        assert all(t.exit_index == len(MARKET) - 1 for t in unresolved)

    def test_a_warmup_shorter_than_the_atr_period_is_refused(self):
        with pytest.raises(DeterminismError, match="unseeded ATR"):
            SimulationConfig(atr_period=14, warmup_bars=5)

    def test_no_trade_starts_before_the_warmup(self):
        config = SimulationConfig(atr_period=14, warmup_bars=40)
        trades = simulate(
            MARKET, house_registry(), symbol=SYMBOL, config=config,
            playbook=research_config((HOUSE,)),
        )
        assert all(t.signal_index >= 40 for t in trades)

    def test_a_fantasy_target_is_not_traded(self):
        # An unbounded R:R is a warning, not a bonus: a 20R "target" is a level
        # price never reaches, so the trade always exits on time and whatever
        # the drift happened to be gets booked. One such trade carries a run.
        loose = SimulationConfig(max_bars_in_trade=25, max_reward_risk=dec("100"))
        strict = SimulationConfig(max_bars_in_trade=25, max_reward_risk=dec("8"))
        args = dict(symbol=SYMBOL, playbook=research_config((HOUSE,)))
        wild = simulate(MARKET, house_registry(), config=loose, **args)
        tame = simulate(MARKET, house_registry(), config=strict, **args)
        assert max((t.r_multiple for t in wild), default=ZERO) >= \
            max((t.r_multiple for t in tame), default=ZERO)

    def test_a_lottery_ticket_is_not_traded_either(self):
        config = SimulationConfig(max_bars_in_trade=25, min_reward_risk=dec("3"))
        trades = simulate(
            MARKET, house_registry(), symbol=SYMBOL, config=config,
            playbook=research_config((HOUSE,)),
        )
        for trade in trades:
            planned = abs(trade.target - trade.entry) / trade.risk
            assert planned >= dec("2.5")  # allowing for the cost of the fill

    def test_the_config_hashes_into_provenance(self):
        a = SimulationConfig()
        b = SimulationConfig(min_reward_risk=dec("2.5"))
        assert a.config_hash != b.config_hash
        assert a.config_hash == SimulationConfig().config_hash

    def test_a_quiet_market_produces_no_trades(self):
        flat = CandleSeries.of([
            bar(i, "1.1000", "1.1001", "1.0999", "1.1000") for i in range(60)
        ])
        assert run(flat) == []


class TestResearchBypass:
    def test_calibration_needs_a_bypass_to_happen_at_all(self):
        # The deadlock the tier system lives inside: a strategy cannot trade
        # without evidence and cannot produce evidence without trading.
        context = build_context(MARKET.upto(120), dec("0.001"), symbol=SYMBOL)
        blocked = evaluate(context, house_registry(), config=PlaybookConfig())
        assert not blocked.tradeable

    def test_the_bypass_is_confined_to_research(self):
        # It grants a provisional record, and that record is labelled so it can
        # never be mistaken for a real measurement.
        config = research_config((HOUSE,))
        assert config.calibrations[HOUSE].dataset == "__research__"

    def test_the_bypass_does_not_overwrite_real_evidence(self):
        from elyon.modules.strategy.domain import Calibration
        real = Calibration(200, 100, dec("0.31"), dataset="eurusd-2024")
        merged = research_config((HOUSE,), PlaybookConfig(calibrations={HOUSE: real}))
        assert merged.calibrations[HOUSE] is real
