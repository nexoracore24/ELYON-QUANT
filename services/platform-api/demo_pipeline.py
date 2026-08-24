"""End-to-end demonstration of the ELYON QUANT decision pipeline.

Feeds a synthetic tick stream through every layer that exists today -- market
data assembly, the six-pillar strategy, scoring, risk budgeting -- and prints
the decision the engine reaches, together with its reasoning.

The strategy is one thesis:

    TENDENCIA · LIQUIDEZ · ORDER BLOCK · FVG · FIBONACCI · ZONA OTE

Run with:  python3 demo_pipeline.py
"""

from __future__ import annotations

from elyon.modules.market_data.domain import (
    AtrProvider,
    BuilderConfig,
    CandleBuilder,
    Tick,
    Timeframe,
)
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.risk.domain import (
    Dimension,
    InstrumentSpec,
    RiskBudget,
    SizingRequest,
    size_position,
)
from elyon.modules.smart_money.domain.structure import Direction
from elyon.modules.strategy.domain import (
    Calibration,
    ConflictPolicy,
    PlaybookConfig,
    StrategyId,
    StrategyRegistry,
    build_context,
    evaluate,
    locate_six_pillars,
    pillar_summary,
    profile,
    score_setup,
)
from elyon.modules.trading.domain import (
    DecisionRecord,
    Provenance,
    Veto,
    explain,
)
from elyon.shared_kernel.edcs import config_hash, dec

SYMBOL = "EURUSD"
TF = Timeframe.M1
SECOND = 1_000_000_000


def candle_ticks(minute: int, o: str, h: str, l: str, c: str) -> list[Tick]:
    """Emit ticks that assemble into exactly this OHLC bar.

    Visiting open, then the far extreme, then the near one, then the close is
    what gives a bar its wick -- and a sweep is nothing but a wick through a
    level, so the demo has to be able to shape one.
    """
    if abs(dec(h) - dec(o)) >= abs(dec(o) - dec(l)):
        route = [o, h, l, c]      # push up first, then reject down
    else:
        route = [o, l, h, c]      # dip first, then recover
    half = dec("0.00005")
    base = minute * 60 * SECOND
    return [
        Tick(
            symbol=SYMBOL,
            event_time_ns=base + step * 10 * SECOND,
            bid=dec(px) - half,
            ask=dec(px) + half,
            provider="demo",
            seq=minute * 10 + step,
            volume=dec("10"),
        )
        for step, px in enumerate(route)
    ]


def stream(bars: list[tuple[str, str, str, str]]) -> list[Tick]:
    ticks: list[Tick] = []
    for minute, (o, h, l, c) in enumerate(bars):
        ticks.extend(candle_ticks(minute, o, h, l, c))
    return ticks


def bullish_setup() -> list[Tick]:
    """Uptrend, a sweep of the lows, then the return to the block.

    Each pullback bar dips below both its neighbours so the swing lows are real
    fractals -- without that alternation there is no structure to read. The
    decision is taken at the moment that matters: liquidity taken, impulsive
    reversal, price back in the block and still in discount.
    """
    return stream([
        ("1.1010", "1.1030", "1.1008", "1.1025"),   # 0 push
        ("1.1025", "1.1028", "1.0995", "1.1020"),   # 1 pullback -> swing low
        ("1.1020", "1.1060", "1.1018", "1.1055"),   # 2 push -> swing high
        ("1.1055", "1.1058", "1.1015", "1.1050"),   # 3 pullback -> higher low
        ("1.1050", "1.1090", "1.1045", "1.1085"),   # 4 push -> higher high
        ("1.1085", "1.1088", "1.1040", "1.1080"),   # 5 pullback -> higher low
        ("1.1080", "1.1120", "1.1075", "1.1115"),   # 6 push -> higher high
        # The sweep: pokes under the last swing low, rejects, closes back above.
        ("1.1115", "1.1118", "1.1035", "1.1070"),   # 7
        # Impulsive recovery: displacement leaving a gap and a block behind.
        ("1.1070", "1.1145", "1.1068", "1.1140"),   # 8
        ("1.1140", "1.1150", "1.1132", "1.1142"),   # 9
        # Retrace into the block, still in the lower half of the leg.
        ("1.1142", "1.1145", "1.1078", "1.1082"),   # 10
        ("1.1082", "1.1086", "1.1072", "1.1080"),   # 11
    ])


def choppy_market() -> list[Tick]:
    """Directionless noise -- the market the engine should refuse to trade."""
    return stream([
        ("1.1000", "1.1012", "1.0994", "1.1004"),
        ("1.1004", "1.1010", "1.0996", "1.1000"),
        ("1.1000", "1.1014", "1.0998", "1.1002"),
        ("1.1002", "1.1008", "1.0992", "1.1006"),
        ("1.1006", "1.1013", "1.0997", "1.0999"),
        ("1.0999", "1.1011", "1.0995", "1.1005"),
        ("1.1005", "1.1009", "1.0993", "1.1001"),
        ("1.1001", "1.1015", "1.0999", "1.1003"),
    ])


def build_candles(ticks: list[Tick]) -> CandleSeries:
    builder = CandleBuilder(SYMBOL, BuilderConfig(timeframe=TF, max_lateness_ns=0))
    confirmed = []
    for tick in ticks:
        confirmed.extend(builder.on_tick(tick).confirmed)
    confirmed.extend(builder.flush())
    return CandleSeries.of(confirmed)


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def run(scenario: str, ticks: list[Tick]) -> None:
    print("\n" + "=" * 68)
    print(f"SCENARIO: {scenario}")
    print("=" * 68)

    # 1. Market data ------------------------------------------------------
    series = build_candles(ticks)
    rule("1. Market data")
    print(f"  {len(ticks)} ticks assembled into {len(series)} confirmed candles")
    print(f"  first candle hash: {series[0].data_hash[:16]}…")
    print(f"  last close: {series[-1].close}")

    atr_provider = AtrProvider(period=5, output_scale=5)
    for candle in series:
        atr_provider.update(candle)
    atr = atr_provider.value or dec("0.0010")
    print(f"  ATR(5): {atr}")

    # 2. The six pillars ---------------------------------------------------
    # One call, one object: the strategy locates all six and reports on each,
    # whether it stands or not.
    setup = locate_six_pillars(series, atr, symbol=SYMBOL, swing_grade=1)

    rule("2. The six pillars")
    print("  " + pillar_summary(setup).replace("\n", "\n  "))
    print(f"\n  {setup.pillars_found}/6 aligned  ·  side: "
          f"{setup.direction.name if setup.direction else 'none'}")

    # 3. The strategy catalog ---------------------------------------------
    # Every active strategy reads the same context and reports independently.
    # Nothing here is calibrated yet, so nothing may open a trade alone -- the
    # gate says so rather than pretending.
    rule("3. Strategy playbook")
    ctx = build_context(series, atr, symbol=SYMBOL, swing_grade=1)
    registry = StrategyRegistry.default()
    verdict = evaluate(ctx, registry)
    print("  " + verdict.summary().replace("\n", "\n  "))
    print(f"\n  gate: {verdict.gate.value}")
    print(f"  {verdict.reason}")

    # 4. What the pillars imply -------------------------------------------
    rule("4. Setup geometry")
    if setup.displacement is not None:
        d = setup.displacement
        print(f"  displacement: {d.direction.name} {d.move} "
              f"over bars {d.start_index}-{d.end_index}")
    for sweep in setup.sweeps:
        side = "sell-side" if sweep.direction is Direction.UP else "buy-side"
        print(f"  sweep at bar {sweep.index}: {side} taken at {sweep.pool.level}"
              f" → implies {sweep.direction.name}")
    if setup.pricing is not None:
        favourable = "favourable" if setup.favourable_pricing else "against us"
        print(f"  price {setup.price} is at a {setup.pricing.value.lower()} "
              f"({favourable})")
    if setup.entry_zone is not None:
        low, high = setup.entry_zone
        print(f"  entry zone (block ∩ OTE): [{low}, {high}]")
    print(f"  invalidation: {setup.invalidation}   target: {setup.target}")

    # 5. Scoring ----------------------------------------------------------
    rule("5. Scoring")
    score = score_setup(setup, vetoes=[
        (Veto.NEWS_WINDOW, False, "no events in window"),
        (Veto.SPREAD_BLOWOUT, False, "spread within profile"),
    ])

    for factor in score.factors:
        mark = "+" if factor.satisfied else " "
        print(f"  [{mark}] {factor.factor.value:<18} {factor.awarded:>3}/{factor.weight:<3}"
              f" {factor.condition}")
    print(f"  {'':<24} {score.total:>3}/100   threshold {score.threshold}")

    # 6. Risk -------------------------------------------------------------
    rule("6. Risk")
    budget = RiskBudget(
        "demo-account",
        {Dimension.DAILY_LOSS: dec("300"), Dimension.TOTAL_OPEN_RISK: dec("200")},
    )

    # The stop comes from the setup's own invalidation, with a buffer: the
    # thesis dies where the swept liquidity is reclaimed, not at a round number.
    price = setup.price
    entry = price
    away = atr * dec("2") if setup.direction is not Direction.DOWN else -atr * dec("2")
    stop = setup.stop_loss(atr * dec("0.3"))
    if stop is None:
        stop = price - away
    target = setup.target if setup.target is not None else price + away * dec("2")

    sizing = size_position(
        SizingRequest(
            equity=dec("10000"),
            risk_fraction=dec("0.005"),
            entry=entry,
            stop_loss=stop,
            take_profit=target,
            spec=InstrumentSpec(
                lot_step=dec("0.01"), min_lot=dec("0.01"),
                max_lot=dec("100"), value_per_price_unit=dec("100000"),
            ),
        ),
        min_reward_risk=dec("2"),
    )

    if sizing.approved:
        print(f"  entry {entry}  stop {stop}  target {target}")
        print(f"  size {sizing.lots} lots, risking {sizing.risk_amount}"
              f" (R:R {sizing.reward_risk:.2f})")
        reservation = budget.reserve(
            intent_id="demo-1",
            amounts={Dimension.DAILY_LOSS: sizing.risk_amount},
            now_ns=0,
        )
        print(f"  budget reserved: {reservation.granted}, "
              f"daily remaining {budget.available(Dimension.DAILY_LOSS)}")
    else:
        print(f"  sizing rejected: {sizing.rejection.value if sizing.rejection else '?'}")
        if sizing.reward_risk is not None:
            print(f"  reward:risk was {sizing.reward_risk:.2f}")

    # 7. Decision ---------------------------------------------------------
    rule("7. Decision")
    tradeable = score.tradeable and sizing.approved
    # Risk only gets the blame when the setup actually reached it. Attributing a
    # 19/100 to the R:R filter would hide the real reason: there was no setup.
    rejection = None
    if score.tradeable and not sizing.approved and sizing.rejection is not None:
        rejection = f"risk:{sizing.rejection.value.lower()}"
    short = setup.direction is Direction.DOWN
    side = "SHORT" if short else "LONG"
    record = DecisionRecord(
        symbol=SYMBOL,
        bar_close_time_ns=series[-1].close_time_ns,
        side=side,
        action=("enter_short" if short else "enter_long") if tradeable else "no_trade",
        score=score,
        provenance=Provenance(
            data_version="demo-dataset-v1",
            config_hash=config_hash({"atrPeriod": 5, "swingGrade": 1, "edcsVersion": 1}),
        ),
        rejection_reason=rejection,
        detected={
            "trend": setup.trend.value,
            "pricing": setup.pricing.value if setup.pricing else "unclassified",
            "pillars": f"{setup.pillars_found}/6",
            "atr": str(atr),
        },
    )
    explanation = explain(record)

    print(f"  decision id: {explanation.decision_id}")
    print(f"  action:      {explanation.action}")
    print(f"  conviction:  {score.conviction.value}")
    print()
    print("  " + explanation.narrative.replace(". ", ".\n  "))

def strategy_catalog() -> None:
    """The catalog, and what switching strategies on actually changes."""
    series = build_candles(bullish_setup())
    provider = AtrProvider(period=5, output_scale=5)
    for candle in series:
        provider.update(candle)
    atr = provider.value or dec("0.0010")
    ctx = build_context(series, atr, symbol=SYMBOL, swing_grade=1)

    print("\n" + "=" * 68)
    print("STRATEGY CATALOG")
    print("=" * 68)
    print("  ● live   ◐ shadow   ○ off")
    print("  🟢 high   🟡 medium   🔴 low   ⚪ unproven\n")
    print("  " + StrategyRegistry.default().summary().replace("\n", "\n  "))

    print("\n" + "-" * 68)
    print("Every tier above is ⚪ UNPROVEN, and that is the point: the catalog")
    print("ships with hypotheses, not blessings. A tier is earned by a")
    print("calibration run, never by the confidence of whoever wrote it.")
    print("-" * 68)

    # A. Nothing calibrated: the house model fires and is still refused.
    rule("A. Default — house model live, nothing calibrated")
    verdict = evaluate(ctx, StrategyRegistry.default())
    print(f"  fired: {[s.strategy.value for s in verdict.fired]}")
    print(f"  gate:  {verdict.gate.value}")
    print(f"  {verdict.reason}")

    # B. Evidence arrives. The same bars, the same signal, a different verdict.
    rule("B. Same bars, after calibration")
    evidence = PlaybookConfig(calibrations={
        StrategyId.SIX_PILLARS: Calibration(
            180, 92, dec("0.42"), dataset="eurusd-m1-2024"
        ),
    })
    tier = evidence.tier_of(StrategyId.SIX_PILLARS)
    print(f"  SIX_PILLARS: 180 trades, 92 wins, expectancy 0.42R "
          f"→ {tier.badge} {tier.value}")
    verdict = evaluate(ctx, StrategyRegistry.default(), config=evidence)
    print(f"  gate:  {verdict.gate.value}")
    print(f"  {verdict.reason}")
    print(f"  confidence: {verdict.confidence}")

    # C. Winning often is not the same as making money.
    rule("C. A strategy that wins 90% of the time and still loses")
    steamroller = PlaybookConfig(calibrations={
        StrategyId.SIX_PILLARS: Calibration(
            200, 180, dec("-0.30"), dataset="eurusd-m1-2024"
        ),
    })
    tier = steamroller.tier_of(StrategyId.SIX_PILLARS)
    print(f"  SIX_PILLARS: 200 trades, 180 wins (90%), expectancy -0.30R "
          f"→ {tier.badge} {tier.value}")
    verdict = evaluate(ctx, StrategyRegistry.default(), config=steamroller)
    print(f"  gate:  {verdict.gate.value}")
    print(f"  {verdict.reason}")

    # D. Two live strategies wanting opposite sides.
    rule("D. When the engine disagrees with itself")
    print("  Netting two opposing signals would put on a small position in")
    print("  whichever was louder and hide that there was no read at all.")
    print(f"  Default policy: {ConflictPolicy.VETO.value} — the engine stands down.")

    # E. Turning strategies on and off.
    rule("E. Toggling — each switch changes the provenance hash")
    for label, reg in [
        ("default            ", StrategyRegistry.default()),
        ("only ICT 2022      ", StrategyRegistry.default().only(
            StrategyId.ICT_2022_MODEL)),
        ("+ Turtle Soup live ", StrategyRegistry.default().live(
            StrategyId.ICT_TURTLE_SOUP)),
        ("everything off     ", StrategyRegistry.all_off()),
    ]:
        print(f"  {label} live={len(reg.live_ids):<2} "
              f"shadow={len(reg.shadow_ids):<2} hash={reg.config_hash[:16]}…")

    print("\n  A replay can prove which strategies were switched on when a")
    print("  trade was taken, because the hash travels with the decision.")


def main() -> None:
    print("=" * 68)
    print("ELYON QUANT — decision pipeline")
    print("=" * 68)

    run("A+ setup (uptrend, sweep, displacement)", bullish_setup())
    run("Choppy market (no edge)", choppy_market())
    strategy_catalog()

    print("\n" + "=" * 68)
    print("Every decision is reproducible: the same ticks, the same config and")
    print("the same active strategies yield the same hashes, score and id.")
    print("=" * 68)


if __name__ == "__main__":
    main()
