"""Deterministic candle assembly.

Ticks arrive out of order and late; candles must not. The builder buffers a
bucket until the watermark clears its close time, then freezes it forever.

Key guarantee (MDE SS0.2, SS20.5): a tick that arrives *before* its bucket is
confirmed is folded in as if it had arrived in order -- so a reordered feed
produces byte-identical candles. A tick that arrives *after* confirmation
never mutates the frozen candle; it is reported as late.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Final

from elyon.shared_kernel.edcs.numeric import dec
from .model import Candle, CandleState, Tick, Timeframe

DEFAULT_MAX_LATENESS_NS: Final[int] = 2 * 1_000_000_000  # 2s


class LateDataPolicy(str, Enum):
    """What to do with a tick that arrives after its candle was confirmed."""

    DROP = "DROP"        # record and discard; the confirmed candle stands
    REVISE = "REVISE"    # emit a new dataset version (never edit in place)


class EmptyCandlePolicy(str, Enum):
    """What to do with a bucket that received no ticks."""

    SKIP = "SKIP"              # the candle simply does not exist
    SYNTHETIC = "SYNTHETIC"    # flat candle at the last close, flagged synthetic


@dataclass(frozen=True, slots=True)
class BuilderConfig:
    timeframe: Timeframe
    price_source: str = "mid"
    max_lateness_ns: int = DEFAULT_MAX_LATENESS_NS
    late_data_policy: LateDataPolicy = LateDataPolicy.DROP
    empty_candle_policy: EmptyCandlePolicy = EmptyCandlePolicy.SKIP


@dataclass(frozen=True, slots=True)
class LateTick:
    """A tick that missed its window. Never lost silently."""

    tick: Tick
    bucket_open_ns: int
    policy: LateDataPolicy


@dataclass(slots=True)
class BuildResult:
    """Outcome of feeding one tick."""

    confirmed: list[Candle] = field(default_factory=list)
    late: LateTick | None = None


@dataclass(slots=True)
class _Bucket:
    """A forming candle plus the assembly bookkeeping it needs.

    ``open`` and ``close`` are defined by the earliest and latest tick by
    *event time*, not by arrival order. Tracking those two timestamps lets a
    tick that arrives out of order slot into the right end in O(1), so a
    reordered feed still produces byte-identical candles (MDE SS20.5).
    """

    candle: Candle
    open_event_ns: int
    close_event_ns: int

    def absorb(self, event_time_ns: int, price: Decimal, volume: Decimal) -> None:
        candle = self.candle
        updates: dict[str, object] = {
            "high": max(candle.high, price),
            "low": min(candle.low, price),
            "volume": candle.volume + volume,
            "tick_count": candle.tick_count + 1,
        }
        if event_time_ns < self.open_event_ns:
            updates["open"] = price
            self.open_event_ns = event_time_ns
        # ">=" so that ties resolve to the most recently applied tick, which
        # matches sequential arrival of same-timestamp quotes.
        if event_time_ns >= self.close_event_ns:
            updates["close"] = price
            self.close_event_ns = event_time_ns
        self.candle = candle.with_updates(**updates)


class CandleBuilder:
    """Assembles ticks of one symbol into candles of one timeframe."""

    def __init__(self, symbol: str, config: BuilderConfig) -> None:
        self._symbol = symbol
        self._config = config
        self._open: dict[int, _Bucket] = {}
        self._watermark_ns: int | None = None
        self._last_confirmed_close_ns: int | None = None
        self._last_close_price: Decimal | None = None

    @property
    def forming(self) -> Candle | None:
        """The newest unconfirmed candle -- provisional, never structural."""
        if not self._open:
            return None
        return self._open[max(self._open)].candle

    def on_tick(self, tick: Tick) -> BuildResult:
        if tick.symbol != self._symbol:
            raise ValueError(f"tick for {tick.symbol} fed to builder of {self._symbol}")

        tf = self._config.timeframe
        bucket = tf.bucket_of(tick.event_time_ns)
        result = BuildResult()

        # The watermark only advances; a late tick cannot pull it backwards.
        candidate = tick.event_time_ns - self._config.max_lateness_ns
        if self._watermark_ns is None or candidate > self._watermark_ns:
            self._watermark_ns = candidate

        if self._last_confirmed_close_ns is not None and bucket < self._last_confirmed_close_ns:
            # Its bucket is already frozen: report, never mutate.
            result.late = LateTick(tick, bucket, self._config.late_data_policy)
            result.confirmed = self._confirm_due()
            return result

        price = tick.price_for(self._config.price_source)
        volume = tick.volume if tick.volume is not None else dec(0)

        existing = self._open.get(bucket)
        if existing is None:
            self._open[bucket] = _Bucket(
                candle=Candle.opening(
                    symbol=self._symbol,
                    timeframe=tf,
                    open_time_ns=bucket,
                    price=price,
                    volume=volume,
                ),
                open_event_ns=tick.event_time_ns,
                close_event_ns=tick.event_time_ns,
            )
        else:
            existing.absorb(tick.event_time_ns, price, volume)

        result.confirmed = self._confirm_due()
        return result

    def flush(self) -> list[Candle]:
        """Confirm every open bucket. For end of stream / end of backtest."""
        confirmed = [self._freeze(b) for b in sorted(self._open)]
        self._open.clear()
        return confirmed

    def _confirm_due(self) -> list[Candle]:
        """Freeze buckets whose close time has cleared the watermark."""
        if self._watermark_ns is None:
            return []
        due = sorted(
            b for b, s in self._open.items()
            if s.candle.close_time_ns <= self._watermark_ns
        )
        return [self._freeze(bucket) for bucket in due]

    def _freeze(self, bucket: int) -> Candle:
        candle = self._open.pop(bucket).candle.confirm()
        self._last_confirmed_close_ns = max(
            self._last_confirmed_close_ns or 0, candle.close_time_ns
        )
        self._last_close_price = candle.close
        return candle

    def fill_gap(self, up_to_open_ns: int) -> list[Candle]:
        """Emit synthetic candles for empty buckets, if the policy asks for it.

        Nothing is ever invented silently: synthetic candles carry the flag and
        a zero tick count, so consumers can tell real trading from stillness.
        """
        if self._config.empty_candle_policy is EmptyCandlePolicy.SKIP:
            return []
        if self._last_confirmed_close_ns is None or self._last_close_price is None:
            return []

        tf = self._config.timeframe
        out: list[Candle] = []
        cursor = self._last_confirmed_close_ns
        while cursor < up_to_open_ns:
            out.append(
                Candle(
                    symbol=self._symbol,
                    timeframe=tf,
                    open_time_ns=cursor,
                    close_time_ns=cursor + tf.duration_ns,
                    open=self._last_close_price,
                    high=self._last_close_price,
                    low=self._last_close_price,
                    close=self._last_close_price,
                    volume=dec(0),
                    tick_count=0,
                    state=CandleState.CONFIRMED,
                    synthetic=True,
                )
            )
            cursor += tf.duration_ns
        self._last_confirmed_close_ns = max(self._last_confirmed_close_ns, cursor)
        return out
