"""Canonical serialization and hashing.

Two objects that are equal must serialize to the same bytes and therefore to
the same hash, on any platform. That is what makes ``dataHash`` / ``configHash``
-- and with them replay and backtest reproducibility -- trustworthy.

Rules (EDCS SS12.2-SS12.5): UTF-8, no insignificant whitespace, object keys sorted
by Unicode code point, decimals as strings (never JSON numbers, which parse as
double), integers as plain JSON numbers, arrays keep their order.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence, TypeVar

from .numeric import DeterminismError, normalize_zero

# Namespace for derived, reproducible identifiers (EDCS SS12.5).
NAMESPACE_ELYON = uuid.UUID("6f0d3f1e-5a2b-5c7d-8e9f-0a1b2c3d4e5f")

T = TypeVar("T")


def canonical_decimal(value: Decimal) -> str:
    """Render a Decimal in canonical form: plain notation, no exponent.

    ``Decimal("1E+2")`` and ``Decimal("100")`` are numerically equal, so they
    must produce the same string -- otherwise equal values would hash apart.
    """
    if not value.is_finite():
        raise DeterminismError(f"NaN/Infinity cannot be serialized: {value!r}")
    text = format(normalize_zero(value), "f")
    return "-0" if text == "-0" else text


def to_canonical(value: Any) -> Any:
    """Recursively convert a value into its canonical JSON-ready form."""
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, bool):  # bool before int: bool is a subclass of int
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise DeterminismError(
            "float cannot be serialized canonically (EDCS SS3.2/SS12.2); use Decimal"
        )
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        # Sorted by Unicode code point so insertion order cannot leak in.
        return {str(k): to_canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [to_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        raise DeterminismError(
            "sets have no canonical order; sort into a list with an explicit "
            "total key first (EDCS SS12.4)"
        )
    if hasattr(value, "to_canonical_dict"):
        return to_canonical(value.to_canonical_dict())
    raise DeterminismError(f"no canonical form for {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize to canonical JSON. Idempotent: serializing twice is identical."""
    return json.dumps(
        to_canonical(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def sha256_hex(value: Any) -> str:
    """SHA-256 over the canonical serialization (EDCS SS12.3)."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def data_hash(value: Any) -> str:
    """Integrity hash of a data object (e.g. a confirmed candle)."""
    return sha256_hex(value)


def config_hash(value: Mapping[str, Any]) -> str:
    """Hash of the effective configuration that shaped an output.

    Anything that changes the result must be inside this hash -- otherwise a
    replay could diverge from the original run with no way to tell why.
    """
    return sha256_hex(value)


def stable_id(*, namespace: str, key: Mapping[str, Any]) -> uuid.UUID:
    """Derive a reproducible UUIDv5 from business keys (EDCS SS12.5).

    The same inputs always yield the same id, so backtest and replay agree.
    Random ids are reserved for places where only uniqueness matters.
    """
    scoped = uuid.uuid5(NAMESPACE_ELYON, namespace)
    return uuid.uuid5(scoped, canonical_json(key))


def sort_canonically(items: Iterable[T], key: Callable[[T], Sequence[Any]]) -> list[T]:
    """Sort by an explicit total key so hashing an unordered set is stable."""
    return sorted(items, key=lambda item: [to_canonical(part) for part in key(item)])
