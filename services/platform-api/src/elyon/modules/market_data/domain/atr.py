"""ATR -- the platform's unit of measure.

Almost every threshold in ELYON QUANT is expressed in multiples of ATR, so this
value has to be identical everywhere or the whole system drifts. It is computed
only from confirmed candles, in the canonical decimal context, with the
accumulator kept at full working precision and quantized only on read.

See: EDCS SS8.3 and docs/04-engines/market-data-engine-bible.md SS12.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from elyon.shared_kernel.edcs.numeric import (
    DeterminismError,
    ZERO,
    ddiv,
    dec,
    dsum,
    quantize,
)
from .model import Candle, CandleState


def true_range(candle: Candle, previous_close: Decimal | None) -> Decimal:
    """Wilder's True Range.

    Without a previous close (the first bar) the gap terms are undefined, so TR
    collapses to the bar's own range.
    """
    span = candle.high - candle.low
    if previous_close is None:
        return span
    return max(span, abs(candle.high - previous_close), abs(candle.low - previous_close))


@dataclass(slots=True)
class AtrProvider:
    """Incremental Wilder ATR over confirmed candles.

    Seeded with the mean of the first ``period`` true ranges, then smoothed by
    ``ATR_i = (ATR_{i-1} * (n-1) + TR_i) / n``. The running value is *not*
    quantized -- quantizing the state would compound rounding error bar after
    bar; only the value handed out is quantized.
    """

    period: int
    output_scale: int
    _atr: Decimal | None = None
    _previous_close: Decimal | None = None
    _seed: list[Decimal] | None = None

    def __post_init__(self) -> None:
        if self.period < 1:
            raise DeterminismError(f"ATR period must be >= 1, got {self.period}")
        if self._seed is None:
            self._seed = []

    @property
    def is_ready(self) -> bool:
        return self._atr is not None

    @property
    def value(self) -> Decimal | None:
        """Current ATR, quantized for output. ``None`` until seeded."""
        if self._atr is None:
            return None
        return quantize(self._atr, self.output_scale)

    def update(self, candle: Candle) -> Decimal | None:
        """Fold in one confirmed candle and return the new ATR (or None)."""
        if candle.state is not CandleState.CONFIRMED:
            raise DeterminismError(
                "ATR is computed from confirmed candles only -- a forming bar "
                "would repaint the indicator (EDCS SS8.3)"
            )

        tr = true_range(candle, self._previous_close)
        self._previous_close = candle.close

        if self._atr is None:
            assert self._seed is not None
            self._seed.append(tr)
            if len(self._seed) < self.period:
                return None
            self._atr = ddiv(dsum(self._seed), dec(self.period))
            self._seed = []
        else:
            n = dec(self.period)
            self._atr = ddiv(self._atr * (n - dec(1)) + tr, n)

        return self.value


def efficiency_ratio(closes: list[Decimal]) -> Decimal:
    """Kaufman Efficiency Ratio: net move over total path.

    Near 1 the market is travelling in a straight line; near 0 it is churning.
    A flat series has no path at all, which would divide by zero -- that is
    defined as 0 (perfectly inefficient) rather than left as NaN (EDCS SS8.5).
    """
    if len(closes) < 2:
        raise DeterminismError("efficiency ratio needs at least two closes")
    net = abs(closes[-1] - closes[0])
    path = dsum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path == ZERO:
        return ZERO
    return quantize(ddiv(net, path), 6)
