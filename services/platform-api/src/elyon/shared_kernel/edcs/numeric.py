"""Canonical deterministic arithmetic.

Implements the ELYON Deterministic Computing Standard (EDCS) numeric rules:
working context is ``decimal128`` (34 significant digits, ROUND_HALF_EVEN),
values are quantized only at output boundaries, and comparisons are exact on
quantized decimals -- no epsilon in the canonical path.

See: docs/08-engineering/deterministic-computing-standard.md (EDCS SS3-SS7).
"""

from __future__ import annotations

from decimal import Decimal, DecimalException, localcontext
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP
from typing import Final, Iterable

# EDCS SS4.1: the canonical working context. Every intermediate uses this.
EDCS_VERSION: Final[int] = 1
WORKING_PRECISION: Final[int] = 34
DEFAULT_ROUNDING: Final[str] = ROUND_HALF_EVEN

# EDCS SS4.2: fixed output scale for dimensionless ratios (ER, fill %, ...).
RATIO_SCALE: Final[int] = 6

ZERO: Final[Decimal] = Decimal(0)
ONE: Final[Decimal] = Decimal(1)


class DeterminismError(ValueError):
    """Raised when a value or operation would break EDCS guarantees."""


def dec(value: str | int | Decimal) -> Decimal:
    """Build a canonical Decimal.

    Floats are rejected on purpose: ``float`` carries binary representation
    error into the decision path, which EDCS SS3.2 forbids. Callers holding a
    float must quantize it at the advisory boundary first (EDCS SS3.4).
    """
    if isinstance(value, float):
        raise DeterminismError(
            "float is not allowed in the deterministic path (EDCS SS3.2); "
            "pass a str/int/Decimal, or quantize at the advisory boundary"
        )
    try:
        result = Decimal(value)
    except (DecimalException, TypeError) as exc:
        raise DeterminismError(f"not a valid decimal: {value!r}") from exc
    if not result.is_finite():
        raise DeterminismError(f"NaN/Infinity are not valid values (EDCS SS6.1): {value!r}")
    return normalize_zero(result)


def normalize_zero(value: Decimal) -> Decimal:
    """Map ``-0`` to ``0`` so equal values always hash and serialize alike."""
    return ZERO if value == ZERO else value


def quantize(value: Decimal, scale: int, rounding: str = DEFAULT_ROUNDING) -> Decimal:
    """Quantize to ``scale`` decimal places at an output boundary.

    EDCS SS4.2: quantize once, at the boundary -- never on intermediates.
    """
    if scale < 0:
        raise DeterminismError(f"scale must be non-negative, got {scale}")
    exponent = Decimal(1).scaleb(-scale)
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        return normalize_zero(value.quantize(exponent, rounding=rounding))


def quantize_ratio(value: Decimal) -> Decimal:
    """Quantize a dimensionless ratio to the fixed 6-decimal scale."""
    return quantize(value, RATIO_SCALE)


def quantize_down(value: Decimal, step: Decimal) -> Decimal:
    """Round *down* onto a ``step`` grid (e.g. broker lot size).

    Conservative by design (EDCS SS5): position sizing must never round up into
    more risk than was approved.
    """
    if step <= ZERO:
        raise DeterminismError(f"step must be positive, got {step}")
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        steps = (value / step).to_integral_value(rounding=ROUND_DOWN)
        return normalize_zero(steps * step)


def quantize_up(value: Decimal, scale: int) -> Decimal:
    """Quantize upward -- the worst case when checking a value against a limit."""
    return quantize(value, scale, rounding=ROUND_UP)


def dsum(values: Iterable[Decimal]) -> Decimal:
    """Sum in the canonical working context, in the given (fixed) order.

    EDCS SS9/SS10: no reassociation, no parallel reduction -- the caller's order
    is the contract, so the result never depends on thread count.
    """
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        total = ZERO
        for value in values:
            total += value
        return normalize_zero(total)


def ddiv(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Divide in the canonical context.

    Degenerate input is the caller's decision to make (EDCS SS8.2): a zero
    denominator raises rather than silently yielding NaN/Infinity.
    """
    if denominator == ZERO:
        raise DeterminismError("division by zero has no canonical result (EDCS SS8.2)")
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        return normalize_zero(numerator / denominator)


def dmul(a: Decimal, b: Decimal) -> Decimal:
    """Multiply in the canonical context."""
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        return normalize_zero(a * b)
