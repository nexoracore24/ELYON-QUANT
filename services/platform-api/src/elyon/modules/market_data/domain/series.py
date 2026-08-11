"""Confirmed candle series -- the read surface detectors work against.

Detectors need indexed, ordered access to *confirmed* candles. Handing them a
raw list invites two classic mistakes: reading a forming bar, or peeking at a
bar that had not closed yet at the moment being evaluated. This type makes both
impossible by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator, Sequence

from elyon.shared_kernel.edcs.numeric import DeterminismError
from .model import Candle, CandleState


@dataclass(frozen=True, slots=True)
class CandleSeries:
    """An immutable, chronologically ordered run of confirmed candles."""

    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        for i, candle in enumerate(self.candles):
            if candle.state is not CandleState.CONFIRMED:
                raise DeterminismError(
                    f"candle {i} is {candle.state.value}; detectors read "
                    "confirmed data only (ENG-002 SS0.2)"
                )
            if i and candle.open_time_ns <= self.candles[i - 1].open_time_ns:
                raise DeterminismError(f"candles out of order at index {i}")

    @classmethod
    def of(cls, candles: Sequence[Candle]) -> CandleSeries:
        return cls(tuple(candles))

    def __len__(self) -> int:
        return len(self.candles)

    def __getitem__(self, index: int) -> Candle:
        return self.candles[index]

    def __iter__(self) -> Iterator[Candle]:
        return iter(self.candles)

    def upto(self, index: int) -> CandleSeries:
        """The series as it stood at ``index`` -- the guard against look-ahead."""
        return CandleSeries(self.candles[: index + 1])

    def highs(self) -> list[Decimal]:
        return [c.high for c in self.candles]

    def lows(self) -> list[Decimal]:
        return [c.low for c in self.candles]

    def closes(self) -> list[Decimal]:
        return [c.close for c in self.candles]
