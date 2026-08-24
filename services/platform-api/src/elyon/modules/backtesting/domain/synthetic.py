"""Synthetic market data, for testing the machinery.

A warning that belongs at the top rather than in a footnote: **running a
strategy on data generated to contain that strategy's setups proves nothing
about the strategy.** The generator knows where the edge is because it put it
there, so the result measures the simulator, not the market.

What this is genuinely good for is exactly that: proving the simulator finds an
edge that is present, finds none when none is present, and reports the
difference. A generator with a known follow-through rate is a test oracle -- if
the simulator says 0.4R on data built to yield roughly 0.4R, the plumbing works.

Every run built here is marked ``Sample.IN_SAMPLE``, and ``calibration_from``
refuses to certify it. That refusal is the feature.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal

from elyon.modules.market_data.domain.model import Candle, CandleState, Timeframe
from elyon.modules.market_data.domain.series import CandleSeries
from elyon.shared_kernel.edcs.numeric import dec, quantize

# 2026-01-15 09:00 New York, so generated bars land inside real session windows
# rather than permanently outside every killzone.
SESSION_BASE_NS = 1768485600_000_000_000


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    """The shape of the market to manufacture.

    ``follow_through`` is the oracle: the fraction of engineered setups that
    actually resolve in their intended direction. Set it to 0.5 and there is no
    edge to find, and a simulator that still reports one has a bug.
    """

    seed: int = 7
    cycles: int = 40
    follow_through: Decimal = dec("0.62")
    start: Decimal = dec("1.10000")
    tick: Decimal = dec("0.00001")
    impulse: Decimal = dec("0.00300")
    noise: Decimal = dec("0.00040")

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "cycles": self.cycles,
            "followThrough": str(self.follow_through),
            "impulse": str(self.impulse),
            "noise": str(self.noise),
        }


class _Builder:
    """Accumulates bars, keeping OHLC coherent as it goes."""

    def __init__(self, symbol: str, timeframe: Timeframe, base_ns: int) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.base_ns = base_ns
        self.bars: list[Candle] = []

    def add(self, o: Decimal, h: Decimal, l: Decimal, c: Decimal) -> None:
        i = len(self.bars)
        start = self.base_ns + i * self.timeframe.duration_ns
        self.bars.append(
            Candle(
                symbol=self.symbol,
                timeframe=self.timeframe,
                open_time_ns=start,
                close_time_ns=start + self.timeframe.duration_ns,
                open=o,
                high=max(h, o, c),
                low=min(l, o, c),
                close=c,
                volume=dec("100"),
                tick_count=20,
                state=CandleState.CONFIRMED,
            )
        )

    @property
    def series(self) -> CandleSeries:
        return CandleSeries.of(self.bars)


def generate(
    config: GeneratorConfig | None = None,
    *,
    symbol: str = "EURUSD",
    timeframe: Timeframe = Timeframe.M1,
) -> CandleSeries:
    """Build a series of engineered Smart Money cycles.

    Each cycle is the story the strategy looks for: a trend leg, a pullback that
    prints a swing, a sweep of that swing, then either the impulsive reversal
    the setup predicts or -- with probability ``1 - follow_through`` -- a
    continuation that stops the trade out. Mixing the two is what makes the
    result a measurement rather than a demonstration.
    """
    settings = config or GeneratorConfig()
    rng = random.Random(settings.seed)
    builder = _Builder(symbol, timeframe, SESSION_BASE_NS)

    price = settings.start
    step = settings.impulse
    noise = settings.noise
    threshold = float(settings.follow_through)

    def jitter() -> Decimal:
        return quantize(
            dec(str(round(rng.uniform(-float(noise), float(noise)), 6))), 5
        )

    for cycle in range(settings.cycles):
        up = cycle % 2 == 0
        sign = dec(1) if up else dec(-1)

        # 1. Two impulse legs with pullbacks between them, so real fractal
        #    swings form. Without the alternation there is no structure to read.
        for _ in range(2):
            top = price + step * sign
            builder.add(price, max(price, top), min(price, top), top)
            price = top

            back = price - step * sign / dec(2)
            builder.add(price, max(price, back), min(price, back), back)
            price = back

        pullback_extreme = price

        # 2. The sweep: poke beyond the pullback swing, then close back inside.
        poke = pullback_extreme - step * sign / dec(3)
        recover = pullback_extreme + jitter()
        builder.add(
            pullback_extreme,
            max(pullback_extreme, poke, recover),
            min(pullback_extreme, poke, recover),
            recover,
        )
        price = recover

        # 3. Resolution. The coin flip is the whole point of the generator.
        resolves = rng.random() < threshold
        direction = sign if resolves else -sign

        # Displacement, leaving a gap and a block behind.
        thrust = price + step * direction
        builder.add(price, max(price, thrust), min(price, thrust), thrust)
        price = thrust

        # Continuation or retracement, so the trade has room to reach a level.
        for _ in range(4):
            drift = price + (step / dec(3)) * direction + jitter()
            builder.add(price, max(price, drift), min(price, drift), drift)
            price = drift

    return builder.series
