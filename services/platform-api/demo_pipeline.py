"""End-to-end demonstration of the ELYON QUANT decision pipeline.

Feeds a synthetic tick stream through every layer that exists today -- market
data assembly, Smart Money detection, scoring, risk budgeting -- and prints the
decision the engine reaches, together with its reasoning.

Run with:  python3 demo_pipeline.py
"""

from __future__ import annotations

from decimal import Decimal

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
from elyon.modules.smart_money.domain import (
    Trend,
    build_pools,
    buy_side,
    build_structure,
    detect_displacement,
    detect_fvg,
    detect_order_block,
    detect_sweeps,
    detect_swings,
    fibonacci_for,
)
from elyon.modules.smart_money.domain.zones import DealingRange, Pricing
from elyon.modules.smart_money.domain.structure import Direction
from elyon.modules.trading.domain import (
    DecisionRecord,
    Factor,
    Provenance,
    ScoreBuilder,
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

    # 2. Structure --------------------------------------------------------
    rule("2. Market structure")
    structure = build_structure(series, grade=1)
    swings = detect_swings(series, grade=1)
    print(f"  trend (incl. sweep): {structure.trend.value}")
    print(f"  swings detected: {len(swings)}")
    if structure.last_high:
        print(f"  last swing high: {structure.last_high.price}")
    if structure.protected_low:
        print(f"  protected low: {structure.protected_low.price}")

    # 3. Liquidity --------------------------------------------------------
    rule("3. Liquidity")
    pools = build_pools(swings, atr)
    print(f"  pools mapped: {len(pools)}")
    sweeps = []
    for i in range(len(series)):
        found = detect_sweeps(series, pools, i, atr)
        if found:
            sweeps.extend(found)
            for s in found:
                side = "sell-side" if s.direction is Direction.UP else "buy-side"
                print(f"  sweep at bar {i}: {side} taken at {s.pool.level}"
                      f" → implies {s.direction.name}")
    if not sweeps:
        print("  no sweeps detected")

    # 4. Points of interest ----------------------------------------------
    rule("4. Points of interest")
    displacement = None
    for i in range(len(series) - 1, 0, -1):
        displacement = detect_displacement(series, i, atr)
        if displacement is not None:
            break

    poi = None
    fvg = None
    if displacement is not None:
        print(f"  displacement: {displacement.direction.name} "
              f"{displacement.move} over bars "
              f"{displacement.start_index}-{displacement.end_index}")
        fvg = detect_fvg(series, displacement.end_index - 1, atr)
        poi = detect_order_block(
            series, displacement,
            has_fvg=fvg is not None,
            had_prior_sweep=bool(sweeps),
        )
        if poi:
            print(f"  order block: [{poi.zone.low}, {poi.zone.high}] "
                  f"state={poi.zone.state.value} confidence={poi.confidence}")
        if fvg:
            print(f"  fair value gap: [{fvg.zone.low}, {fvg.zone.high}] "
                  f"CE={fvg.consequent_encroachment}")
        else:
            print("  no fair value gap in the displacement")
    else:
        print("  no displacement found")

    # 5. Pricing ----------------------------------------------------------
    rule("5. Pricing")
    # The dealing range is the last impulsive leg, not the whole window:
    # premium and discount only mean something relative to the move in play.
    if displacement is not None:
        leg = [series[i] for i in range(displacement.start_index, len(series))]
        leg_low = min(c.low for c in leg)
        leg_high = max(c.high for c in leg)
    else:
        leg_low = min(c.low for c in series)
        leg_high = max(c.high for c in series)
    dealing_range = DealingRange(leg_low, leg_high, Direction.UP, 0, len(series) - 1)
    price = series[-1].close
    pricing = dealing_range.classify(price)
    fib = fibonacci_for(dealing_range)
    print(f"  dealing range: [{dealing_range.low}, {dealing_range.high}]")
    print(f"  price {price} sits at {dealing_range.position_of(price):.3f} → {pricing.value}")
    if fib:
        print(f"  OTE band: [{fib.ote_low}, {fib.ote_high}] optimal={fib.ote_optimal}")

    # 6. Scoring ----------------------------------------------------------
    rule("6. Scoring")
    builder = ScoreBuilder()

    # A sweep prints a lower low by design, so reading bias from the bars that
    # include it would mistake the manipulation for a trend change. The bias
    # comes from the structure before it, re-confirmed by the displacement.
    # The last sweep is the one that set up this trade; earlier pokes are noise.
    key_sweep = max(sweeps, key=lambda s_: s_.index) if sweeps else None
    pre_sweep = (
        build_structure(series.upto(key_sweep.index - 1), grade=1)
        if key_sweep is not None and key_sweep.index >= 4 else structure
    )
    bias_confirmed = (
        pre_sweep.trend is Trend.BULLISH
        and displacement is not None
        and displacement.direction is Direction.UP
    )
    if bias_confirmed:
        builder.award(
            Factor.HTF_BIAS,
            f"{pre_sweep.trend.value} before the sweep, displacement agrees",
        )
    else:
        builder.withhold(Factor.HTF_BIAS, f"pre-sweep structure {pre_sweep.trend.value}")

    if displacement is not None:
        builder.award(Factor.STRUCTURE, f"displacement {displacement.move}")
    else:
        builder.withhold(Factor.STRUCTURE, "no displacement")

    if sweeps:
        builder.award(Factor.LIQUIDITY_SWEEP, f"{len(sweeps)} sweep(s)")
    else:
        builder.withhold(Factor.LIQUIDITY_SWEEP, "no sweep")

    if poi is not None:
        builder.award(Factor.POI_QUALITY, f"order block conf {poi.confidence}")
    else:
        builder.withhold(Factor.POI_QUALITY, "no POI")

    if fvg is not None:
        builder.award(Factor.IMBALANCE, "FVG present")
    else:
        builder.withhold(Factor.IMBALANCE, "no FVG")

    if pricing is Pricing.DISCOUNT:
        builder.award(Factor.PRICING, "in discount")
    else:
        builder.withhold(Factor.PRICING, f"in {pricing.value.lower()}")

    if fib and fib.in_ote(price):
        builder.award(Factor.OTE_FIBONACCI, "inside OTE")
    else:
        builder.withhold(Factor.OTE_FIBONACCI, "outside OTE band")

    builder.withhold(Factor.VOLUME, "synthetic feed, volume not meaningful")

    if pools:
        builder.award(Factor.TARGET_LIQUIDITY, f"{len(pools)} pools as targets")
    else:
        builder.withhold(Factor.TARGET_LIQUIDITY, "no target liquidity")

    builder.check_veto(Veto.NEWS_WINDOW, False, "no events in window")
    builder.check_veto(Veto.SPREAD_BLOWOUT, False, "spread within profile")
    score = builder.build()

    for factor in score.factors:
        mark = "+" if factor.satisfied else " "
        print(f"  [{mark}] {factor.factor.value:<18} {factor.awarded:>3}/{factor.weight:<3}"
              f" {factor.condition}")
    print(f"  {'':<24} {score.total:>3}/100   threshold {score.threshold}")

    # 7. Risk -------------------------------------------------------------
    rule("7. Risk")
    budget = RiskBudget(
        "demo-account",
        {Dimension.DAILY_LOSS: dec("300"), Dimension.TOTAL_OPEN_RISK: dec("200")},
    )

    entry = price
    buffer = atr * dec("0.3")
    if key_sweep is not None:
        stop = series[key_sweep.index].low - buffer
    elif poi:
        stop = poi.zone.low - buffer
    else:
        stop = price - atr * dec("2")

    # Target the furthest buy-side pool: the significant liquidity the move is
    # actually reaching for, not the first level it happens to pass.
    targets = buy_side(pools, price)
    target = max((t.level for t in targets), default=dealing_range.high)

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

    # 8. Decision ---------------------------------------------------------
    rule("8. Decision")
    tradeable = score.tradeable and sizing.approved
    rejection = None
    if not tradeable and sizing.rejection is not None:
        rejection = f"risk:{sizing.rejection.value.lower()}"
    record = DecisionRecord(
        symbol=SYMBOL,
        bar_close_time_ns=series[-1].close_time_ns,
        side="LONG",
        action="enter_long" if tradeable else "no_trade",
        score=score,
        provenance=Provenance(
            data_version="demo-dataset-v1",
            config_hash=config_hash({"atrPeriod": 5, "swingGrade": 1, "edcsVersion": 1}),
        ),
        rejection_reason=rejection,
        detected={
            "trend": structure.trend.value,
            "pricing": pricing.value,
            "atr": str(atr),
        },
    )
    explanation = explain(record)

    print(f"  decision id: {explanation.decision_id}")
    print(f"  action:      {explanation.action}")
    print(f"  conviction:  {score.conviction.value}")
    print()
    print("  " + explanation.narrative.replace(". ", ".\n  "))

def main() -> None:
    print("=" * 68)
    print("ELYON QUANT — decision pipeline")
    print("=" * 68)

    run("A+ setup (uptrend, sweep, displacement)", bullish_setup())
    run("Choppy market (no edge)", choppy_market())

    print("\n" + "=" * 68)
    print("Both decisions are reproducible: the same ticks and the same config")
    print("yield the same candle hashes, the same score and the same id.")
    print("=" * 68)


if __name__ == "__main__":
    main()
