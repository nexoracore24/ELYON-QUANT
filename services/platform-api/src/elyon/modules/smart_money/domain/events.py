"""Structural events: BOS, CHoCH and MSS.

Implements D08/D09/D10 of the Smart Money Engine Bible.

The distinction these three encode is the one that decides bias, and getting it
backwards inverts every downstream decision:

* **BOS** breaks the *continuation* extreme -- the trend carries on.
* **CHoCH** breaks the *protected* swing against the trend -- the first warning
  that the trend may be over.
* **MSS** is a CHoCH that followed through -- the confirmation.

A break without displacement is not a break: it is usually a sweep dressed up as
one, which is why displacement is required by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final

from elyon.modules.market_data.domain.series import CandleSeries
from .structure import Direction, Displacement, Structure, Trend, detect_displacement

DEFAULT_FAILURE_BARS: Final[int] = 3
DEFAULT_MSS_CONFIRM_BARS: Final[int] = 10


class BreakConfirmation(str, Enum):
    """What counts as breaking a level."""

    CLOSE = "CLOSE"  # body closes beyond it -- strict, the default
    WICK = "WICK"    # a wick suffices -- permissive


class EventKind(str, Enum):
    BOS = "BOS"
    CHOCH = "CHOCH"
    MSS = "MSS"


@dataclass(frozen=True, slots=True)
class StructuralEvent:
    """A confirmed break of structure."""

    kind: EventKind
    direction: Direction
    level: Decimal
    index: int
    displacement: Displacement | None
    swept_liquidity: bool = False

    @property
    def is_weak(self) -> bool:
        """A break with no impulse behind it -- suspect until proven otherwise."""
        return self.displacement is None


def _breaks(
    candle, level: Decimal, direction: Direction, confirmation: BreakConfirmation
) -> bool:
    """Strictly beyond the level -- touching it exactly is not a break."""
    if direction is Direction.UP:
        price = candle.close if confirmation is BreakConfirmation.CLOSE else candle.high
        return price > level
    price = candle.close if confirmation is BreakConfirmation.CLOSE else candle.low
    return price < level


def detect_bos(
    series: CandleSeries,
    structure: Structure,
    index: int,
    atr: Decimal,
    *,
    confirmation: BreakConfirmation = BreakConfirmation.CLOSE,
    require_displacement: bool = True,
) -> StructuralEvent | None:
    """D08 -- continuation: price breaks the extreme in the trend's direction."""
    if structure.trend is Trend.BULLISH:
        reference, direction = structure.last_high, Direction.UP
    elif structure.trend is Trend.BEARISH:
        reference, direction = structure.last_low, Direction.DOWN
    else:
        return None  # no trend to continue

    if reference is None or index <= reference.confirm_index:
        return None
    if not _breaks(series[index], reference.price, direction, confirmation):
        return None

    displacement = detect_displacement(series, index, atr)
    if require_displacement and (
        displacement is None or displacement.direction is not direction
    ):
        return None

    return StructuralEvent(EventKind.BOS, direction, reference.price, index, displacement)


def detect_choch(
    series: CandleSeries,
    structure: Structure,
    index: int,
    atr: Decimal,
    *,
    confirmation: BreakConfirmation = BreakConfirmation.CLOSE,
    require_displacement: bool = True,
    swept_liquidity: bool = False,
) -> StructuralEvent | None:
    """D09 -- change of character: the protected swing gives way.

    Measured against the protected swing (the HL holding a bullish trend up, the
    LH capping a bearish one), never against the most recent minor swing.
    """
    if structure.trend is Trend.BULLISH:
        reference, direction = structure.protected_low, Direction.DOWN
    elif structure.trend is Trend.BEARISH:
        reference, direction = structure.protected_high, Direction.UP
    else:
        return None  # in a range the first break is an MSS, not a CHoCH

    if reference is None or index <= reference.confirm_index:
        return None
    if not _breaks(series[index], reference.price, direction, confirmation):
        return None

    displacement = detect_displacement(series, index, atr)
    if require_displacement and (
        displacement is None or displacement.direction is not direction
    ):
        return None

    return StructuralEvent(
        EventKind.CHOCH, direction, reference.price, index, displacement, swept_liquidity
    )


def event_failed(
    series: CandleSeries,
    event: StructuralEvent,
    *,
    failure_bars: int = DEFAULT_FAILURE_BARS,
) -> bool:
    """Did price immediately close back through the broken level?

    A break that is reclaimed within a few bars was a trap, not a transition.
    """
    end = min(event.index + failure_bars, len(series) - 1)
    for i in range(event.index + 1, end + 1):
        close = series[i].close
        if event.direction is Direction.UP and close < event.level:
            return True
        if event.direction is Direction.DOWN and close > event.level:
            return True
    return False


@dataclass(frozen=True, slots=True)
class MarketStructureShift:
    """D10 -- a CHoCH that earned confirmation."""

    direction: Direction
    choch_index: int
    confirm_index: int


def detect_mss(
    series: CandleSeries,
    choch: StructuralEvent,
    follow_through: StructuralEvent | None,
    *,
    confirm_bars: int = DEFAULT_MSS_CONFIRM_BARS,
) -> MarketStructureShift | None:
    """D10 -- promote a CHoCH to a shift once price follows through.

    CHoCH and MSS are not synonyms: every MSS begins as a CHoCH, but a CHoCH
    that never follows through is just a failed break. Waiting for the BOS in
    the new direction is what separates the two.
    """
    if choch.kind is not EventKind.CHOCH:
        raise ValueError(f"expected a CHoCH, got {choch.kind.value}")
    if follow_through is None or follow_through.kind is not EventKind.BOS:
        return None
    if follow_through.direction is not choch.direction:
        return None
    if not (choch.index < follow_through.index <= choch.index + confirm_bars):
        return None

    return MarketStructureShift(choch.direction, choch.index, follow_through.index)
