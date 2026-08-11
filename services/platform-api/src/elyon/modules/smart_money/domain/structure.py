"""Market structure detectors: displacement and swing points.

Implements D01 (Displacement), D03/D04 (Swing High/Low) and D05/D06 (External /
Internal Structure) of the Smart Money Engine Bible.

Everything here is causal: a swing at index ``i`` of grade ``k`` cannot be known
until bar ``i + k`` has closed. The detectors report that confirmation lag
explicitly instead of quietly using future bars -- which is how a backtest ends
up looking better than live trading ever will.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final, Sequence

from elyon.modules.market_data.domain.series import CandleSeries
from elyon.shared_kernel.edcs.numeric import ZERO, dec, dsum

DEFAULT_DISPLACEMENT_ATR_MULT: Final[str] = "1.5"
DEFAULT_DISPLACEMENT_BODY_RATIO: Final[str] = "0.6"
DEFAULT_DISPLACEMENT_MAX_BARS: Final[int] = 3


class Direction(int, Enum):
    UP = 1
    DOWN = -1

    @property
    def opposite(self) -> "Direction":
        return Direction.DOWN if self is Direction.UP else Direction.UP


class SwingLabel(str, Enum):
    """How a swing sits relative to the previous swing of its own kind."""

    HH = "HH"  # higher high
    LH = "LH"  # lower high
    HL = "HL"  # higher low
    LL = "LL"  # lower low


class Trend(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGE = "RANGE"
    UNDETERMINED = "UNDETERMINED"


@dataclass(frozen=True, slots=True)
class Displacement:
    """An impulsive move -- the signature of intent rather than drift."""

    start_index: int
    end_index: int
    direction: Direction
    move: Decimal
    body_ratio: Decimal


@dataclass(frozen=True, slots=True)
class Swing:
    """A confirmed swing point."""

    index: int
    price: Decimal
    is_high: bool
    grade: int
    confirm_index: int
    label: SwingLabel | None = None

    @property
    def is_low(self) -> bool:
        return not self.is_high


def detect_displacement(
    series: CandleSeries,
    end_index: int,
    atr: Decimal,
    *,
    atr_mult: Decimal | None = None,
    body_ratio: Decimal | None = None,
    max_bars: int = DEFAULT_DISPLACEMENT_MAX_BARS,
    min_bars: int = 1,
) -> Displacement | None:
    """D01 -- detect an impulsive leg ending at ``end_index``.

    A move qualifies when it travels far enough relative to ATR *and* is carried
    by bodies rather than wicks *and* the bars broadly agree on direction. The
    shortest qualifying window wins, because the cleanest expression of the move
    is the one that best marks where the impulse actually began.
    """
    mult = atr_mult if atr_mult is not None else dec(DEFAULT_DISPLACEMENT_ATR_MULT)
    min_body = body_ratio if body_ratio is not None else dec(DEFAULT_DISPLACEMENT_BODY_RATIO)
    if atr <= ZERO:
        return None

    threshold = mult * atr
    for length in range(min_bars, max_bars + 1):
        start = end_index - length + 1
        if start < 0:
            continue

        window = [series[i] for i in range(start, end_index + 1)]
        net = window[-1].close - window[0].open
        if net == ZERO:
            continue
        direction = Direction.UP if net > ZERO else Direction.DOWN

        if abs(net) < threshold:
            continue

        total_range = dsum(c.high - c.low for c in window if c.high > c.low)
        if total_range == ZERO:
            continue
        ratio = dsum(abs(c.close - c.open) for c in window) / total_range
        if ratio < min_body:
            continue

        # Most of the window must pull the same way; one counter-bar inside a
        # three-bar thrust is normal, a mixed bag is not an impulse.
        agreeing = sum(
            1 for c in window
            if (c.close > c.open and direction is Direction.UP)
            or (c.close < c.open and direction is Direction.DOWN)
        )
        if agreeing * 3 < len(window) * 2:  # >= 2/3, integer-exact
            continue

        return Displacement(start, end_index, direction, abs(net), ratio)
    return None


def detect_swings(series: CandleSeries, grade: int, *, strict: bool = True) -> list[Swing]:
    """D03/D04 -- fractal swing highs and lows of the given grade.

    A bar is a swing high when its high dominates the ``grade`` bars on either
    side. With ``strict`` (the default) a plateau of equal highs produces no
    swing; those level tops are the raw material of equal highs, which is a
    liquidity concept, not a structural one.
    """
    if grade < 1:
        raise ValueError(f"swing grade must be >= 1, got {grade}")

    swings: list[Swing] = []
    for i in range(grade, len(series) - grade):
        candle = series[i]
        neighbours = range(1, grade + 1)

        is_high = all(
            (candle.high > series[i - j].high if strict else candle.high >= series[i - j].high)
            and (candle.high > series[i + j].high if strict else candle.high >= series[i + j].high)
            for j in neighbours
        )
        if is_high:
            swings.append(Swing(i, candle.high, True, grade, i + grade))
            continue

        is_low = all(
            (candle.low < series[i - j].low if strict else candle.low <= series[i - j].low)
            and (candle.low < series[i + j].low if strict else candle.low <= series[i + j].low)
            for j in neighbours
        )
        if is_low:
            swings.append(Swing(i, candle.low, False, grade, i + grade))

    return label_swings(swings)


def label_swings(swings: Sequence[Swing]) -> list[Swing]:
    """Tag each swing HH/LH/HL/LL against the previous swing of the same kind."""
    labelled: list[Swing] = []
    last_high: Swing | None = None
    last_low: Swing | None = None

    for swing in swings:
        label: SwingLabel | None = None
        if swing.is_high:
            if last_high is not None:
                label = SwingLabel.HH if swing.price > last_high.price else SwingLabel.LH
            tagged = Swing(
                swing.index, swing.price, True, swing.grade, swing.confirm_index, label
            )
            last_high = tagged
        else:
            if last_low is not None:
                label = SwingLabel.HL if swing.price > last_low.price else SwingLabel.LL
            tagged = Swing(
                swing.index, swing.price, False, swing.grade, swing.confirm_index, label
            )
            last_low = tagged
        labelled.append(tagged)

    return labelled


@dataclass(frozen=True, slots=True)
class Structure:
    """D05/D06 -- the structural read of a series at a point in time."""

    trend: Trend
    swings: tuple[Swing, ...]

    @property
    def highs(self) -> list[Swing]:
        return [s for s in self.swings if s.is_high]

    @property
    def lows(self) -> list[Swing]:
        return [s for s in self.swings if s.is_low]

    @property
    def last_high(self) -> Swing | None:
        return self.highs[-1] if self.highs else None

    @property
    def last_low(self) -> Swing | None:
        return self.lows[-1] if self.lows else None

    @property
    def protected_low(self) -> Swing | None:
        """The higher low a bullish trend is standing on.

        Breaking it is what turns continuation into a change of character, so
        this -- not the most recent low -- is the level CHoCH measures against.
        """
        for swing in reversed(self.lows):
            if swing.label is SwingLabel.HL:
                return swing
        return self.last_low

    @property
    def protected_high(self) -> Swing | None:
        """Mirror of :attr:`protected_low` for a bearish trend."""
        for swing in reversed(self.highs):
            if swing.label is SwingLabel.LH:
                return swing
        return self.last_high


def build_structure(
    series: CandleSeries, grade: int, *, min_swings_per_side: int = 2
) -> Structure:
    """Classify trend from swing labels.

    Trend is a consequence of structure, never of a moving average: a market is
    bullish because it is making higher highs and higher lows, and it stops
    being bullish when that sequence breaks.
    """
    swings = detect_swings(series, grade)
    highs = [s for s in swings if s.is_high and s.label is not None]
    lows = [s for s in swings if s.is_low and s.label is not None]

    if len(highs) < min_swings_per_side or len(lows) < min_swings_per_side:
        return Structure(Trend.UNDETERMINED, tuple(swings))

    recent_highs = highs[-min_swings_per_side:]
    recent_lows = lows[-min_swings_per_side:]

    if all(s.label is SwingLabel.HH for s in recent_highs) and all(
        s.label is SwingLabel.HL for s in recent_lows
    ):
        return Structure(Trend.BULLISH, tuple(swings))
    if all(s.label is SwingLabel.LH for s in recent_highs) and all(
        s.label is SwingLabel.LL for s in recent_lows
    ):
        return Structure(Trend.BEARISH, tuple(swings))
    return Structure(Trend.RANGE, tuple(swings))
