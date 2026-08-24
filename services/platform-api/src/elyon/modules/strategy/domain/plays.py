"""The plays themselves.

Each function reads the shared context and returns exactly one signal. They are
deliberately small and independent: a play may not call another play, and may
not look at what anything else concluded. Independence is what makes confluence
mean something later -- two plays that consult each other are one play wearing
two names, and counting them twice manufactures conviction out of nothing.

Every play abstains with a reason. None of them size a position or decide
anything; they report what they see and hand it to the playbook.
"""

from __future__ import annotations

from decimal import Decimal

from elyon.modules.smart_money.domain.events import detect_bos, detect_choch
from elyon.modules.smart_money.domain.liquidity import LiquidityType
from elyon.modules.smart_money.domain.structure import Direction
from elyon.shared_kernel.edcs.numeric import ZERO, dec

from .catalog import StrategyId
from .patterns import (
    all_fvgs,
    detect_bpr,
    detect_breaker,
    overlaps,
    penetration_ratio,
    unfilled,
)
from .sessions import Killzone
from .signals import StrategyContext, StrategySignal, abstain, fire

# How near price must be to a zone, in ATR, to count as "at" it.
NEAR_ZONE_ATR = dec("0.5")


def _recent(context: StrategyContext, index: int, bars: int) -> bool:
    """Did this happen recently enough to still be the reason for a trade?"""
    return context.index - index <= bars


# ---------------------------------------------------------------------------
# The house model
# ---------------------------------------------------------------------------

def six_pillars(context: StrategyContext) -> StrategySignal:
    """All six aligned. The strictest read the engine has, and the rarest."""
    setup = context.setup
    if setup.direction is None:
        return abstain(StrategyId.SIX_PILLARS, "no side: no trend and no sweep")

    # Four of six is the floor: without the block and the imbalance there is no
    # zone to trade, only a direction to guess at.
    if setup.pillars_found < 4:
        return abstain(
            StrategyId.SIX_PILLARS,
            f"only {setup.pillars_found}/6 aligned "
            f"({', '.join(p.value for p in setup.missing)})",
        )

    confidence = dec(setup.pillars_found) / dec(6)
    return fire(
        StrategyId.SIX_PILLARS,
        setup.direction,
        confidence,
        f"{setup.pillars_found}/6 pillars aligned",
        evidence=tuple(f.detail for f in setup.findings if f.found),
        entry_zone=setup.entry_zone,
        invalidation=setup.invalidation,
        target=setup.target,
    )


# ---------------------------------------------------------------------------
# ICT structural models
# ---------------------------------------------------------------------------

def ict_2022_model(context: StrategyContext) -> StrategySignal:
    """Sweep, then a shift against it, then the gap the shift left behind.

    The sequence is the strategy. A shift without a preceding raid is just a
    trend change; a raid without a shift is a failed reversal. Only the order
    sweep → shift → gap says that someone took liquidity in order to move.
    """
    if not context.sweeps:
        return abstain(StrategyId.ICT_2022_MODEL, "no liquidity taken yet")

    sweep = max(context.sweeps, key=lambda s: s.index)
    direction = sweep.direction

    shift = _structure_shift_after(context, sweep.index, direction)
    if shift is None:
        return abstain(
            StrategyId.ICT_2022_MODEL,
            f"liquidity taken at {sweep.pool.level} but structure has not "
            f"shifted {direction.name} yet",
        )

    gaps = [
        g for g in all_fvgs(context.series, context.atr, since=sweep.index)
        if g.direction is direction and unfilled(context.series, g)
    ]
    if not gaps:
        return abstain(
            StrategyId.ICT_2022_MODEL,
            f"shift confirmed at bar {shift} but it left no unfilled gap to "
            f"enter on",
        )

    gap = gaps[-1]
    return fire(
        StrategyId.ICT_2022_MODEL,
        direction,
        "0.9",
        f"sweep at {sweep.pool.level} → shift at bar {shift} → FVG entry",
        evidence=(
            f"sweep bar {sweep.index} at {sweep.pool.level}",
            f"structure shift bar {shift}",
            f"FVG [{gap.zone.low}, {gap.zone.high}]",
        ),
        entry_zone=(gap.zone.low, gap.zone.high),
        invalidation=sweep.pool.level,
        target=context.setup.target,
    )


def _structure_shift_after(
    context: StrategyContext, after: int, direction: Direction
) -> int | None:
    """The first confirmed CHoCH or BOS in ``direction`` following ``after``."""
    for i in range(after + 1, len(context.series)):
        choch = detect_choch(
            context.series, context.structure, i, context.atr, swept_liquidity=True
        )
        if choch is not None and choch.direction is direction:
            return i
        bos = detect_bos(context.series, context.structure, i, context.atr)
        if bos is not None and bos.direction is direction:
            return i
    return None


def ict_turtle_soup(context: StrategyContext) -> StrategySignal:
    """An old extreme breaks, fails, and closes back inside.

    The breakout traders who bought the high are the liquidity that funds the
    move down. What makes it a soup rather than a breakout is the close: back
    inside the range, on the bar that broke it.
    """
    if not context.sweeps:
        return abstain(StrategyId.ICT_TURTLE_SOUP, "no level has been raided")

    sweep = max(context.sweeps, key=lambda s: s.index)
    if not _recent(context, sweep.index, 3):
        return abstain(
            StrategyId.ICT_TURTLE_SOUP,
            f"the raid at bar {sweep.index} is {context.index - sweep.index} "
            f"bars old; the failure has stopped being news",
        )

    # An old level is a better trap than a fresh one: it has had time to
    # accumulate the stops that make the reversal worth taking.
    age = sweep.index - min(sweep.pool.indices, default=sweep.index)
    if age < 3:
        return abstain(
            StrategyId.ICT_TURTLE_SOUP,
            f"the level raided was only {age} bars old -- too new to be holding "
            f"meaningful stops",
        )

    confidence = "0.75" if sweep.pool.touches > 1 else "0.6"
    return fire(
        StrategyId.ICT_TURTLE_SOUP,
        sweep.direction,
        confidence,
        f"failed break of a {age}-bar-old level at {sweep.pool.level}",
        evidence=(
            f"level {sweep.pool.level} touched {sweep.pool.touches}x",
            f"penetration {sweep.penetration} then reclaimed",
        ),
        invalidation=sweep.pool.level,
        target=context.setup.target,
    )


def equal_level_raid(context: StrategyContext) -> StrategySignal:
    """Equal highs or lows are an advertisement, and price collects them."""
    equal = [s for s in context.sweeps if s.pool.touches >= 2]
    if not equal:
        return abstain(
            StrategyId.EQUAL_LEVEL_RAID,
            "no multi-touch level has been taken"
            if context.sweeps
            else "no equal highs or lows have been raided",
        )

    sweep = max(equal, key=lambda s: s.index)
    if not _recent(context, sweep.index, 4):
        return abstain(
            StrategyId.EQUAL_LEVEL_RAID,
            f"the raid at bar {sweep.index} is stale",
        )

    side = "highs" if sweep.pool.type is LiquidityType.BSL else "lows"
    return fire(
        StrategyId.EQUAL_LEVEL_RAID,
        sweep.direction,
        "0.7",
        f"equal {side} ({sweep.pool.touches} touches) taken at {sweep.pool.level}",
        evidence=(f"touched at bars {list(sweep.pool.indices)}",),
        invalidation=sweep.pool.level,
        target=context.setup.target,
    )


# ---------------------------------------------------------------------------
# Zone models
# ---------------------------------------------------------------------------

def ict_unicorn(context: StrategyContext) -> StrategySignal:
    """A breaker and a fair value gap on the same prices.

    Two independent reasons for one zone. The name is about how rarely they
    line up, and the rarity is the point: this is not a pattern to go looking
    for, it is one to recognise.
    """
    direction = context.setup.direction
    if direction is None:
        return abstain(StrategyId.ICT_UNICORN, "no directional bias to anchor to")

    breaker = detect_breaker(context.series, context.atr, direction)
    if breaker is None:
        return abstain(StrategyId.ICT_UNICORN, "no breaker block has formed")

    gaps = [
        g for g in all_fvgs(context.series, context.atr)
        if g.direction is direction and unfilled(context.series, g)
    ]
    for gap in reversed(gaps):
        shared = overlaps(breaker.zone, gap.zone)
        if shared is not None:
            return fire(
                StrategyId.ICT_UNICORN,
                direction,
                "0.85",
                f"breaker and FVG overlap on [{shared[0]}, {shared[1]}]",
                evidence=(
                    f"breaker [{breaker.zone.low}, {breaker.zone.high}]",
                    f"FVG [{gap.zone.low}, {gap.zone.high}]",
                ),
                entry_zone=shared,
                invalidation=breaker.zone.low if direction is Direction.UP
                else breaker.zone.high,
                target=context.setup.target,
            )

    return abstain(
        StrategyId.ICT_UNICORN,
        f"breaker at [{breaker.zone.low}, {breaker.zone.high}] but no FVG "
        f"overlaps it",
    )


def breaker_retest(context: StrategyContext) -> StrategySignal:
    """The level that did not hold is the level that now rejects."""
    direction = context.setup.direction
    if direction is None:
        return abstain(StrategyId.BREAKER_RETEST, "no directional bias")

    breaker = detect_breaker(context.series, context.atr, direction)
    if breaker is None:
        return abstain(StrategyId.BREAKER_RETEST, "no block has failed and flipped")

    distance = min(
        abs(context.price - breaker.zone.low),
        abs(context.price - breaker.zone.high),
    )
    inside = breaker.zone.low <= context.price <= breaker.zone.high
    if not inside and distance > NEAR_ZONE_ATR * context.atr:
        return abstain(
            StrategyId.BREAKER_RETEST,
            f"breaker at [{breaker.zone.low}, {breaker.zone.high}] but price "
            f"{context.price} has not come back to it",
        )

    depth = penetration_ratio(breaker.zone, context.price)
    return fire(
        StrategyId.BREAKER_RETEST,
        direction,
        "0.7" if inside else "0.55",
        f"price back at the failed block, {depth} into it",
        evidence=(f"breaker [{breaker.zone.low}, {breaker.zone.high}]",),
        entry_zone=(breaker.zone.low, breaker.zone.high),
        invalidation=breaker.zone.low if direction is Direction.UP
        else breaker.zone.high,
        target=context.setup.target,
    )


def balanced_price_range(context: StrategyContext) -> StrategySignal:
    """Two opposing gaps overlapping: a pocket price tends to revisit."""
    bpr = detect_bpr(context.series, context.atr)
    if bpr is None:
        return abstain(
            StrategyId.BALANCED_PRICE_RANGE, "no opposing gaps overlap"
        )

    distance = min(
        abs(context.price - bpr.zone.low), abs(context.price - bpr.zone.high)
    )
    if distance > NEAR_ZONE_ATR * context.atr and not (
        bpr.zone.low <= context.price <= bpr.zone.high
    ):
        return abstain(
            StrategyId.BALANCED_PRICE_RANGE,
            f"BPR at [{bpr.zone.low}, {bpr.zone.high}] is not in play at "
            f"{context.price}",
        )

    return fire(
        StrategyId.BALANCED_PRICE_RANGE,
        bpr.direction,
        "0.5",
        f"balanced price range [{bpr.zone.low}, {bpr.zone.high}] in play",
        evidence=(
            f"bullish FVG [{bpr.bullish.zone.low}, {bpr.bullish.zone.high}]",
            f"bearish FVG [{bpr.bearish.zone.low}, {bpr.bearish.zone.high}]",
        ),
        entry_zone=(bpr.zone.low, bpr.zone.high),
        target=context.setup.target,
    )


def ict_ote(context: StrategyContext) -> StrategySignal:
    """Retracement into 0.618-0.786 of an impulsive leg.

    Deliberately weak on its own. A retracement level is a price, not a reason,
    and the tier system is what stops this from opening trades by itself.
    """
    fib = context.setup.fibonacci
    direction = context.setup.direction
    if fib is None or direction is None:
        return abstain(StrategyId.ICT_OTE, "no impulsive leg to measure")

    if not fib.in_ote(context.price):
        return abstain(
            StrategyId.ICT_OTE,
            f"price {context.price} outside [{fib.ote_low}, {fib.ote_high}]",
        )

    # Closer to 0.705 is better; the edges of the band are worth less.
    span = fib.ote_high - fib.ote_low
    offset = abs(context.price - fib.ote_optimal)
    closeness = ZERO if span == ZERO else (span - min(offset, span)) / span
    confidence = dec("0.35") + dec("0.25") * closeness

    return fire(
        StrategyId.ICT_OTE,
        direction,
        confidence,
        f"price {context.price} inside OTE, optimal {fib.ote_optimal}",
        evidence=(f"leg {fib.origin} → {fib.destination}",),
        entry_zone=(fib.ote_low, fib.ote_high),
        invalidation=fib.origin,
        target=fib.destination,
    )


# ---------------------------------------------------------------------------
# Session models -- the clock is part of the strategy
# ---------------------------------------------------------------------------

def ict_silver_bullet(context: StrategyContext) -> StrategySignal:
    """One hour, one gap, in the direction of the bias.

    Outside the hour this is just "there is an FVG", which is true most of the
    time and predicts nothing. The window is not a filter on the strategy, it
    *is* the strategy.
    """
    if not context.in_killzone(
        Killzone.SILVER_BULLET_AM, Killzone.SILVER_BULLET_PM
    ):
        return abstain(
            StrategyId.ICT_SILVER_BULLET,
            f"outside the Silver Bullet hours (currently "
            f"{context.killzone.value})",
        )

    direction = context.setup.direction
    if direction is None:
        return abstain(
            StrategyId.ICT_SILVER_BULLET, "in the window but with no bias to trade"
        )

    gaps = [
        g for g in all_fvgs(context.series, context.atr)
        if g.direction is direction
        and unfilled(context.series, g)
        and _recent(context, g.zone.origin_index, 6)
    ]
    if not gaps:
        return abstain(
            StrategyId.ICT_SILVER_BULLET,
            "in the window with a bias but no fresh gap in that direction",
        )

    gap = gaps[-1]
    return fire(
        StrategyId.ICT_SILVER_BULLET,
        direction,
        "0.75",
        f"{context.killzone.value}: fresh {direction.name} FVG",
        evidence=(f"FVG [{gap.zone.low}, {gap.zone.high}]",),
        entry_zone=(gap.zone.low, gap.zone.high),
        invalidation=context.setup.invalidation,
        target=context.setup.target,
    )


def ict_judas_swing(context: StrategyContext) -> StrategySignal:
    """The opening move is a lie.

    Price runs one way at the open to collect stops, then spends the session
    going the other. What identifies it is a raid early in the session that has
    already been reclaimed.
    """
    if not context.in_killzone(Killzone.LONDON_OPEN, Killzone.NY_AM):
        return abstain(
            StrategyId.ICT_JUDAS_SWING,
            f"outside the session-open windows (currently "
            f"{context.killzone.value})",
        )

    today = context.bars_of_local_day()
    if len(today) < 3:
        return abstain(
            StrategyId.ICT_JUDAS_SWING,
            f"only {len(today)} bars into the local session -- too early to "
            f"call the open a lie",
        )

    early = set(today[: max(2, len(today) // 3)])
    raids = [s for s in context.sweeps if s.index in early]
    if not raids:
        return abstain(
            StrategyId.ICT_JUDAS_SWING, "the session open took no liquidity"
        )

    raid = max(raids, key=lambda s: s.index)
    reclaimed = (
        context.price > raid.pool.level
        if raid.direction is Direction.UP
        else context.price < raid.pool.level
    )
    if not reclaimed:
        return abstain(
            StrategyId.ICT_JUDAS_SWING,
            f"the open raided {raid.pool.level} but price has not reclaimed it",
        )

    return fire(
        StrategyId.ICT_JUDAS_SWING,
        raid.direction,
        "0.7",
        f"session open raided {raid.pool.level} and reversed",
        evidence=(f"raid at bar {raid.index}", f"reclaimed by {context.price}"),
        invalidation=raid.pool.level,
        target=context.setup.target,
    )


def ict_power_of_3(context: StrategyContext) -> StrategySignal:
    """Accumulate, manipulate, distribute.

    The session builds a range, breaks one side of it to collect stops, and
    then travels the other way. All three phases have to be visible; two of
    them is just a range with a poke in it.
    """
    today = context.bars_of_local_day()
    if len(today) < 5:
        return abstain(
            StrategyId.ICT_POWER_OF_3,
            f"only {len(today)} bars into the session -- accumulation is not "
            f"established",
        )

    third = max(2, len(today) // 3)
    accumulation = today[:third]
    bars = [context.series[i] for i in accumulation]
    range_high = max(c.high for c in bars)
    range_low = min(c.low for c in bars)

    manipulation = [
        s for s in context.sweeps if s.index in set(today[third:])
    ]
    if not manipulation:
        return abstain(
            StrategyId.ICT_POWER_OF_3,
            f"range [{range_low}, {range_high}] built but neither side has "
            f"been taken",
        )

    raid = max(manipulation, key=lambda s: s.index)
    direction = raid.direction

    # Distribution: price has to have left the range in the implied direction.
    distributing = (
        context.price > range_high
        if direction is Direction.UP
        else context.price < range_low
    )
    if not distributing:
        return abstain(
            StrategyId.ICT_POWER_OF_3,
            f"manipulation at {raid.pool.level} but price {context.price} is "
            f"still inside [{range_low}, {range_high}] -- no distribution yet",
        )

    return fire(
        StrategyId.ICT_POWER_OF_3,
        direction,
        "0.75",
        f"AMD complete: range [{range_low}, {range_high}], raid at "
        f"{raid.pool.level}, now distributing {direction.name}",
        evidence=(
            f"accumulation over {len(accumulation)} bars",
            f"manipulation at bar {raid.index}",
        ),
        invalidation=raid.pool.level,
        target=context.setup.target,
    )


def asian_range_sweep(context: StrategyContext) -> StrategySignal:
    """London takes one side of the overnight range before choosing a day."""
    if not context.in_killzone(Killzone.LONDON_OPEN):
        return abstain(
            StrategyId.ASIAN_RANGE_SWEEP,
            f"not the London open (currently {context.killzone.value})",
        )

    asian = [
        i for i in range(len(context.series))
        if context.clock.in_killzone(
            context.series[i].close_time_ns, Killzone.ASIA
        )
    ]
    if len(asian) < 3:
        return abstain(
            StrategyId.ASIAN_RANGE_SWEEP,
            f"only {len(asian)} Asian-session bars in the window -- no range "
            f"to sweep",
        )

    bars = [context.series[i] for i in asian]
    high, low = max(c.high for c in bars), min(c.low for c in bars)

    took_highs = context.candle.high > high and context.price < high
    took_lows = context.candle.low < low and context.price > low
    if not (took_highs or took_lows):
        return abstain(
            StrategyId.ASIAN_RANGE_SWEEP,
            f"Asian range [{low}, {high}] still intact",
        )

    direction = Direction.DOWN if took_highs else Direction.UP
    side = "high" if took_highs else "low"
    return fire(
        StrategyId.ASIAN_RANGE_SWEEP,
        direction,
        "0.7",
        f"London swept the Asian {side} and rejected",
        evidence=(f"Asian range [{low}, {high}] over {len(asian)} bars",),
        invalidation=high if took_highs else low,
        target=context.setup.target,
    )


# ---------------------------------------------------------------------------
# Not runnable yet, and saying so
# ---------------------------------------------------------------------------

def smt_divergence(context: StrategyContext) -> StrategySignal:
    """Correlated instruments disagreeing at an extreme.

    Genuinely one of the strongest tells there is, and genuinely impossible to
    compute from one symbol. Rather than approximate it into something that
    would fire on noise, it abstains and says why. The registry refuses to run
    it LIVE for the same reason.
    """
    return abstain(
        StrategyId.SMT_DIVERGENCE,
        "needs a correlated instrument feed; only one symbol is available",
    )


PLAYS = {
    StrategyId.SIX_PILLARS: six_pillars,
    StrategyId.ICT_2022_MODEL: ict_2022_model,
    StrategyId.ICT_SILVER_BULLET: ict_silver_bullet,
    StrategyId.ICT_TURTLE_SOUP: ict_turtle_soup,
    StrategyId.ICT_UNICORN: ict_unicorn,
    StrategyId.ICT_JUDAS_SWING: ict_judas_swing,
    StrategyId.ICT_OTE: ict_ote,
    StrategyId.ICT_POWER_OF_3: ict_power_of_3,
    StrategyId.BREAKER_RETEST: breaker_retest,
    StrategyId.BALANCED_PRICE_RANGE: balanced_price_range,
    StrategyId.EQUAL_LEVEL_RAID: equal_level_raid,
    StrategyId.ASIAN_RANGE_SWEEP: asian_range_sweep,
    StrategyId.SMT_DIVERGENCE: smt_divergence,
}
