"""Managing a position after it fills.

Entering is the easy half. What decides whether a strategy with an edge actually
compounds is what happens next: when the stop moves, when part of the position
comes off, when a trade that is going nowhere gets closed to free the capital.

One rule dominates this module and is worth stating before anything else:

    **A stop never moves against the position. Ever.**

A "trailing" stop that can widen is not a trailing stop -- it is a stop somebody
moved because they did not like being wrong, and it is the single most reliable
way to turn a bounded loss into an unbounded one. Every function here that
returns a stop is checked against the one it replaces, and a backwards move is
refused rather than logged.

Everything is expressed in **R** -- multiples of the risk originally taken -- so
the same rules apply unchanged to a 5-pip stop on EURUSD and a $30 stop on gold.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from typing import Final, Sequence

from elyon.modules.market_data.domain.model import Candle
from elyon.modules.smart_money.domain.structure import Direction
from elyon.shared_kernel.edcs.numeric import (
    ZERO,
    DeterminismError,
    dec,
    quantize,
)


class ManagementAction(str, Enum):
    """What the manager decided to do on this bar."""

    HOLD = "HOLD"
    MOVE_STOP = "MOVE_STOP"
    TAKE_PARTIAL = "TAKE_PARTIAL"
    CLOSE = "CLOSE"


class CloseReason(str, Enum):
    STOP_HIT = "STOP_HIT"
    TARGET_HIT = "TARGET_HIT"
    TIME_STOP = "TIME_STOP"
    MANUAL = "MANUAL"
    SESSION_END = "SESSION_END"


@dataclass(frozen=True, slots=True)
class ManagementPolicy:
    """The rules, all in R so they travel across instruments unchanged."""

    # Move the stop to entry once the trade is this far ahead. Set to None to
    # never break even -- some strategies are hurt by it, because a stop at
    # entry is a stop sitting exactly where price likes to retest.
    break_even_at_r: Decimal | None = dec("1.0")
    # A small cushion beyond entry, so break-even actually covers the round
    # turn. A stop exactly at entry still loses the spread.
    break_even_buffer_r: Decimal = dec("0.1")

    # Once ahead by this much, trail. Trailing from the start strangles trades
    # before they have room to work.
    trail_from_r: Decimal | None = dec("1.5")
    trail_distance_atr: Decimal = dec("1.5")

    # Take this fraction off at this many R.
    partial_at_r: Decimal | None = dec("1.5")
    partial_fraction: Decimal = dec("0.5")

    # Close a trade that has gone nowhere: capital tied up in a trade that is
    # not working is capital not available for one that is.
    time_stop_bars: int | None = 40
    time_stop_min_r: Decimal = dec("0.3")

    def __post_init__(self) -> None:
        if not ZERO <= self.partial_fraction <= dec("1"):
            raise DeterminismError(
                f"partial fraction {self.partial_fraction} outside [0, 1]"
            )
        if self.trail_from_r is not None and self.break_even_at_r is not None:
            if self.trail_from_r < self.break_even_at_r:
                raise DeterminismError(
                    f"trailing starts at {self.trail_from_r}R, before break-even "
                    f"at {self.break_even_at_r}R; the trail would move the stop "
                    f"backwards from break-even"
                )


@dataclass(frozen=True, slots=True)
class ManagedPosition:
    """A live position and everything done to it so far.

    Immutable: managing it returns a new one, so the state that justified a
    decision stays attached to that decision.
    """

    symbol: str
    direction: Direction
    entry: Decimal
    initial_stop: Decimal
    stop: Decimal
    target: Decimal
    quantity: Decimal
    opened_at_index: int
    bars_held: int = 0
    realized_r: Decimal = ZERO
    closed_quantity: Decimal = ZERO
    broke_even: bool = False
    partial_taken: bool = False
    closed: bool = False
    close_reason: CloseReason | None = None
    peak_r: Decimal = ZERO
    journal: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.initial_risk == ZERO:
            raise DeterminismError(
                f"{self.symbol}: entry and stop are the same price; the "
                f"position has no defined risk and nothing can be measured in R"
            )
        long = self.direction is Direction.UP
        if long and self.initial_stop >= self.entry:
            raise DeterminismError(
                f"long stop {self.initial_stop} at or above entry {self.entry}"
            )
        if not long and self.initial_stop <= self.entry:
            raise DeterminismError(
                f"short stop {self.initial_stop} at or below entry {self.entry}"
            )

    # -- measurement ------------------------------------------------------

    @property
    def initial_risk(self) -> Decimal:
        """The unit everything is measured in. Fixed at entry, never rebased.

        Rebasing R after moving the stop would make a trade look better simply
        because it was managed -- 1R would keep shrinking, and every result
        would inflate.
        """
        return abs(self.entry - self.initial_stop)

    @property
    def open_quantity(self) -> Decimal:
        return self.quantity - self.closed_quantity

    @property
    def sign(self) -> Decimal:
        return dec(int(self.direction.value))

    def r_at(self, price: Decimal) -> Decimal:
        """How many R the position is ahead (or behind) at this price."""
        return quantize((price - self.entry) * self.sign / self.initial_risk, 4)

    def locked_r(self) -> Decimal:
        """The worst outcome still possible, given where the stop sits now.

        Positive once the stop is past entry: the number that says the trade can
        no longer lose.
        """
        return quantize((self.stop - self.entry) * self.sign / self.initial_risk, 4)

    @property
    def is_risk_free(self) -> bool:
        return self.locked_r() >= ZERO

    @property
    def total_r(self) -> Decimal:
        """Realized so far, ignoring what is still open."""
        return self.realized_r

    # -- moving the stop --------------------------------------------------

    def _is_forward(self, new_stop: Decimal) -> bool:
        """Would this move tighten the stop, or loosen it?"""
        if self.direction is Direction.UP:
            return new_stop > self.stop
        return new_stop < self.stop

    def with_stop(self, new_stop: Decimal, note: str) -> "ManagedPosition":
        """Move the stop, or refuse.

        The refusal is the feature. A stop that can move backwards under any
        circumstance is not a risk limit, it is a suggestion.
        """
        if not self._is_forward(new_stop):
            raise DeterminismError(
                f"{self.symbol}: refusing to move stop from {self.stop} to "
                f"{new_stop} -- that widens the risk on a "
                f"{self.direction.name} position"
            )
        return replace(
            self, stop=new_stop,
            journal=self.journal + (f"stop → {new_stop} ({note})",),
        )


@dataclass(frozen=True, slots=True)
class ManagementDecision:
    """What to do, and why."""

    action: ManagementAction
    position: ManagedPosition
    reason: str
    close_quantity: Decimal = ZERO
    new_stop: Decimal | None = None

    def __str__(self) -> str:
        return f"{self.action.value}: {self.reason}"


def open_position(
    *,
    symbol: str,
    direction: Direction,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    quantity: Decimal,
    at_index: int,
) -> ManagedPosition:
    return ManagedPosition(
        symbol=symbol, direction=direction, entry=entry,
        initial_stop=stop, stop=stop, target=target,
        quantity=quantity, opened_at_index=at_index,
        journal=(f"opened {direction.name} {quantity} @ {entry}, stop {stop}",),
    )


def manage(
    position: ManagedPosition,
    candle: Candle,
    atr: Decimal,
    *,
    policy: ManagementPolicy | None = None,
) -> ManagementDecision:
    """Decide what happens to a position on one confirmed candle.

    Order matters and is not arbitrary. Exits are checked before improvements,
    because a bar that hits the stop has already ended the trade -- moving the
    stop on it would be reacting to a bar the position did not survive.

    And, as in the backtester: when one bar contains both the stop and the
    target, the stop is assumed. OHLC cannot say which came first, and the safe
    assumption is the unfavourable one.
    """
    rules = policy or ManagementPolicy()

    if position.closed:
        return ManagementDecision(
            ManagementAction.HOLD, position, "position already closed"
        )

    advanced = replace(position, bars_held=position.bars_held + 1)
    long = position.direction is Direction.UP

    # 1. Did this bar end the trade? Stop wins ties, deliberately.
    hit_stop = candle.low <= position.stop if long else candle.high >= position.stop
    hit_target = candle.high >= position.target if long else candle.low <= position.target

    if hit_stop:
        return _close(advanced, position.stop, CloseReason.STOP_HIT)
    if hit_target:
        return _close(advanced, position.target, CloseReason.TARGET_HIT)

    # 2. Track the best the trade has been. Excursion is measured on the wick,
    #    not the close: the stop is a resting order and would have been filled.
    excursion = candle.high if long else candle.low
    reached = advanced.r_at(excursion)
    advanced = replace(advanced, peak_r=max(advanced.peak_r, reached))

    # 3. Take a partial before touching the stop. Doing it the other way round
    #    would size the remainder against a stop that has already moved, which
    #    quietly changes what "half the position" means.
    if (
        rules.partial_at_r is not None
        and not advanced.partial_taken
        and reached >= rules.partial_at_r
    ):
        return _take_partial(advanced, rules, reached)

    # 4. Break even, then trail. Never the reverse: trailing from below entry
    #    would move the stop backwards the moment break-even is applied.
    if (
        rules.break_even_at_r is not None
        and not advanced.broke_even
        and reached >= rules.break_even_at_r
    ):
        return _break_even(advanced, rules, reached)

    if rules.trail_from_r is not None and reached >= rules.trail_from_r:
        trailed = _trail(advanced, rules, candle, atr)
        if trailed is not None:
            return trailed

    # 5. A trade going nowhere is capital not available to one that is.
    if (
        rules.time_stop_bars is not None
        and advanced.bars_held >= rules.time_stop_bars
        and advanced.peak_r < rules.time_stop_min_r
    ):
        return _close(
            advanced, candle.close, CloseReason.TIME_STOP,
            detail=f"{advanced.bars_held} bars, never beyond "
                   f"{advanced.peak_r}R",
        )

    return ManagementDecision(
        ManagementAction.HOLD, advanced,
        f"holding at {advanced.r_at(candle.close)}R "
        f"(locked {advanced.locked_r()}R)",
    )


def _close(
    position: ManagedPosition,
    price: Decimal,
    reason: CloseReason,
    *,
    detail: str = "",
) -> ManagementDecision:
    remaining = position.open_quantity
    share = remaining / position.quantity if position.quantity else ZERO
    realized = position.realized_r + position.r_at(price) * share

    closed = replace(
        position,
        closed=True,
        close_reason=reason,
        closed_quantity=position.quantity,
        realized_r=quantize(realized, 4),
        journal=position.journal + (f"closed @ {price} ({reason.value})",),
    )
    return ManagementDecision(
        ManagementAction.CLOSE, closed,
        f"{reason.value} at {price}" + (f" -- {detail}" if detail else ""),
        close_quantity=remaining,
    )


def _take_partial(
    position: ManagedPosition, rules: ManagementPolicy, reached: Decimal
) -> ManagementDecision:
    """Bank part of the trade at the level that triggered it.

    Credited at the trigger price, not at the bar's close: the order would have
    been resting there and filled on the way through. Using the close would
    credit whatever the bar happened to do afterwards.
    """
    price = position.entry + position.initial_risk * rules.partial_at_r * position.sign
    quantity = quantize(position.open_quantity * rules.partial_fraction, 8)
    if quantity <= ZERO:
        return ManagementDecision(
            ManagementAction.HOLD, position, "partial would be zero-sized"
        )

    share = quantity / position.quantity
    realized = position.realized_r + rules.partial_at_r * share

    updated = replace(
        position,
        partial_taken=True,
        closed_quantity=position.closed_quantity + quantity,
        realized_r=quantize(realized, 4),
        journal=position.journal + (
            f"took {quantity} off at {rules.partial_at_r}R",
        ),
    )
    return ManagementDecision(
        ManagementAction.TAKE_PARTIAL, updated,
        f"banked {rules.partial_fraction} of the position at "
        f"{rules.partial_at_r}R",
        close_quantity=quantity,
    )


def _break_even(
    position: ManagedPosition, rules: ManagementPolicy, reached: Decimal
) -> ManagementDecision:
    """Move the stop past entry.

    Past, not to. A stop exactly at entry still loses the round turn, so
    "break-even" that does not cover costs is a small loss wearing the name of
    a scratch.
    """
    offset = position.initial_risk * rules.break_even_buffer_r
    new_stop = position.entry + offset * position.sign

    if not position._is_forward(new_stop):
        return ManagementDecision(
            ManagementAction.HOLD, replace(position, broke_even=True),
            "stop already beyond break-even",
        )

    moved = replace(
        position.with_stop(new_stop, f"break-even at {reached}R"),
        broke_even=True,
    )
    return ManagementDecision(
        ManagementAction.MOVE_STOP, moved,
        f"reached {reached}R; stop to {new_stop}, locking "
        f"{moved.locked_r()}R",
        new_stop=new_stop,
    )


def _trail(
    position: ManagedPosition,
    rules: ManagementPolicy,
    candle: Candle,
    atr: Decimal,
) -> ManagementDecision | None:
    """Follow price at a fixed ATR distance, one way only."""
    distance = atr * rules.trail_distance_atr
    if position.direction is Direction.UP:
        candidate = candle.high - distance
    else:
        candidate = candle.low + distance

    if not position._is_forward(candidate):
        return None  # the trail is behind the current stop; leave it alone

    moved = position.with_stop(candidate, f"trailing {rules.trail_distance_atr}×ATR")
    return ManagementDecision(
        ManagementAction.MOVE_STOP, moved,
        f"trailing to {candidate}, locking {moved.locked_r()}R",
        new_stop=candidate,
    )
