"""Canonical market data model.

The single source of truth for prices in ELYON QUANT. Everything downstream --
Smart Money detectors, context, risk, execution -- reads these types and never
talks to a broker directly.

See: docs/04-engines/market-data-engine-bible.md
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Any, Final

from elyon.shared_kernel.edcs.canonical import data_hash
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

NANOS_PER_SECOND: Final[int] = 1_000_000_000


class CandleState(str, Enum):
    """Lifecycle of a candle.

    ``FORMING`` is mutable and must never reach a structural detector.
    ``CONFIRMED`` is frozen forever -- this is where no-repaint comes from.
    """

    FORMING = "FORMING"
    CONFIRMED = "CONFIRMED"
    REVISED = "REVISED"


class Timeframe(str, Enum):
    """Supported timeframes, with their duration in nanoseconds."""

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"

    @property
    def duration_ns(self) -> int:
        return {
            Timeframe.M1: 60,
            Timeframe.M5: 300,
            Timeframe.M15: 900,
            Timeframe.H1: 3600,
            Timeframe.H4: 14400,
            Timeframe.D1: 86400,
        }[self] * NANOS_PER_SECOND

    def bucket_of(self, event_time_ns: int) -> int:
        """Open time of the bucket that owns ``event_time_ns``.

        Buckets sit on a fixed UTC grid rather than starting at the first tick,
        so the same input always lands in the same candle. Intervals are
        half-open ``[open, close)``: a tick exactly on a close belongs to the
        next candle.
        """
        return (event_time_ns // self.duration_ns) * self.duration_ns


@dataclass(frozen=True, slots=True)
class Tick:
    """A normalized quote. Immutable once ingested."""

    symbol: str
    event_time_ns: int
    bid: Decimal
    ask: Decimal
    provider: str
    seq: int
    volume: Decimal | None = None

    def __post_init__(self) -> None:
        if self.bid <= 0 or self.ask <= 0:
            raise DeterminismError(f"non-positive price: bid={self.bid} ask={self.ask}")
        if self.bid > self.ask:
            raise DeterminismError(f"crossed quote: bid={self.bid} > ask={self.ask}")
        if self.event_time_ns < 0:
            raise DeterminismError(f"negative event time: {self.event_time_ns}")

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    def price_for(self, source: str) -> Decimal:
        """Price used to build candles. Fixed per dataset, never mid-session."""
        if source == "bid":
            return self.bid
        if source == "ask":
            return self.ask
        if source == "mid":
            return self.mid
        raise DeterminismError(f"unknown candle price source: {source!r}")


@dataclass(frozen=True, slots=True)
class Candle:
    """An OHLCV bar.

    Frozen dataclass: state transitions return a new instance, so a confirmed
    candle can never be mutated in place by an out-of-order tick.
    """

    symbol: str
    timeframe: Timeframe
    open_time_ns: int
    close_time_ns: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    tick_count: int
    state: CandleState = CandleState.FORMING
    synthetic: bool = False

    def __post_init__(self) -> None:
        if self.close_time_ns <= self.open_time_ns:
            raise DeterminismError("close_time must be after open_time")
        if self.high < self.low:
            raise DeterminismError(f"high {self.high} below low {self.low}")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise DeterminismError("OHLC inconsistent: body outside high/low range")

    def with_updates(self, **changes: Any) -> Candle:
        """Return a new forming candle with ``changes`` applied.

        Only a forming candle may change: once confirmed, a candle is frozen
        for good, which is where no-repaint comes from (MDE SS0.1).
        """
        if self.state is not CandleState.FORMING:
            raise DeterminismError(
                f"cannot modify a {self.state.value} candle -- "
                "confirmed data is immutable (MDE SS0.1)"
            )
        return replace(self, **changes)

    def apply(self, price: Decimal, volume: Decimal) -> Candle:
        """Fold an in-order tick into a forming candle."""
        return self.with_updates(
            high=max(self.high, price),
            low=min(self.low, price),
            close=price,
            volume=self.volume + volume,
            tick_count=self.tick_count + 1,
        )

    def confirm(self) -> Candle:
        """Freeze the candle. Idempotent."""
        if self.state is CandleState.CONFIRMED:
            return self
        if self.state is not CandleState.FORMING:
            raise DeterminismError(f"cannot confirm a {self.state.value} candle")
        return replace(self, state=CandleState.CONFIRMED)

    def to_canonical_dict(self) -> dict[str, Any]:
        """Canonical form used for hashing and for crossing a contract."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe.value,
            "openTime": self.open_time_ns,
            "closeTime": self.close_time_ns,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "tickCount": self.tick_count,
            "state": self.state.value,
            "synthetic": self.synthetic,
        }

    @property
    def data_hash(self) -> str:
        return data_hash(self.to_canonical_dict())

    @classmethod
    def opening(
        cls,
        *,
        symbol: str,
        timeframe: Timeframe,
        open_time_ns: int,
        price: Decimal,
        volume: Decimal | None = None,
    ) -> Candle:
        """Open a new forming candle at ``price``."""
        vol = volume if volume is not None else dec(0)
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            open_time_ns=open_time_ns,
            close_time_ns=open_time_ns + timeframe.duration_ns,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=vol,
            tick_count=1,
        )
