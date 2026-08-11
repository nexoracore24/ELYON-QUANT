"""EDCS conformance tests.

These are the tests that turn "bit-for-bit reproducible" from a claim into a
property the build enforces. Numbering follows EDCS SS14.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from elyon.shared_kernel.edcs import (
    DeterminismError,
    canonical_json,
    config_hash,
    data_hash,
    ddiv,
    dec,
    dsum,
    quantize,
    quantize_down,
    quantize_ratio,
    stable_id,
)


class TestNumericFoundations:
    def test_float_is_rejected_in_the_deterministic_path(self):
        # A float would smuggle binary representation error into a decision.
        with pytest.raises(DeterminismError, match="float is not allowed"):
            dec(0.1)  # type: ignore[arg-type]

    def test_decimal_arithmetic_is_exact(self):
        # The canonical counter-example to binary floating point.
        assert dec("0.1") + dec("0.2") == dec("0.3")
        assert 0.1 + 0.2 != 0.3  # what we are protecting the platform from

    def test_nan_and_infinity_are_not_values(self):
        for bad in ("NaN", "Infinity", "-Infinity"):
            with pytest.raises(DeterminismError):
                dec(bad)

    def test_negative_zero_normalizes(self):
        assert canonical_json(dec("-0")) == canonical_json(dec("0"))

    def test_t2_rounding_is_half_even(self):
        # Banker's rounding: ties go to even, so long sums stay unbiased.
        assert quantize(dec("2.5"), 0) == dec("2")
        assert quantize(dec("3.5"), 0) == dec("4")
        assert quantize(dec("-2.5"), 0) == dec("-2")

    def test_lot_sizing_rounds_down(self):
        # Never round up into more risk than was approved.
        assert quantize_down(dec("0.257"), dec("0.01")) == dec("0.25")
        assert quantize_down(dec("0.999"), dec("0.1")) == dec("0.9")

    def test_t1_summation_order_is_the_contract(self):
        values = [dec("0.1"), dec("0.2"), dec("0.3")]
        assert dsum(values) == dec("0.6")
        # Decimal is exact here, so order cannot change the result at all --
        # which is the point: no reassociation risk, unlike binary floats.
        assert dsum(reversed(values)) == dsum(values)

    def test_division_by_zero_has_no_canonical_result(self):
        with pytest.raises(DeterminismError, match="division by zero"):
            ddiv(dec("1"), dec("0"))

    def test_ratios_use_the_fixed_six_decimal_scale(self):
        assert quantize_ratio(ddiv(dec("1"), dec("3"))) == dec("0.333333")


class TestCanonicalSerialization:
    def test_t8_key_order_cannot_leak_into_the_hash(self):
        a = {"symbol": "EURUSD", "close": dec("1.0850"), "tf": "M15"}
        b = {"tf": "M15", "close": dec("1.0850"), "symbol": "EURUSD"}
        assert canonical_json(a) == canonical_json(b)
        assert data_hash(a) == data_hash(b)

    def test_t9_decimals_serialize_as_strings(self):
        # As a JSON number this would be parsed back as a double and lose value.
        assert canonical_json({"p": dec("1.08500")}) == '{"p":"1.08500"}'
        assert canonical_json({"v": dec("0.1") + dec("0.2")}) == '{"v":"0.3"}'

    def test_equal_decimals_serialize_identically(self):
        # 1E+2 and 100 are numerically equal, so they must hash alike.
        assert canonical_json(Decimal("1E+2")) == canonical_json(Decimal("100"))

    def test_serialization_is_idempotent(self):
        payload = {"a": [dec("1.5"), 2, True, None], "b": {"c": "x"}}
        once = canonical_json(payload)
        assert once == canonical_json(payload)

    def test_floats_cannot_be_serialized(self):
        with pytest.raises(DeterminismError):
            canonical_json({"p": 1.085})

    def test_sets_have_no_canonical_order(self):
        with pytest.raises(DeterminismError, match="no canonical order"):
            canonical_json({"levels": {dec("1"), dec("2")}})

    def test_t13_config_hash_tracks_anything_that_changes_output(self):
        base = {"atrPeriod": 14, "edcsVersion": 1}
        assert config_hash(base) == config_hash(dict(base))
        assert config_hash(base) != config_hash({**base, "atrPeriod": 21})


class TestStableIds:
    def test_t10_same_business_keys_yield_the_same_id(self):
        key = {"symbol": "EURUSD", "barCloseTime": 1690000900000000000}
        assert stable_id(namespace="decision", key=key) == stable_id(
            namespace="decision", key=key
        )

    def test_different_keys_and_namespaces_diverge(self):
        k1 = {"symbol": "EURUSD", "barCloseTime": 1}
        k2 = {"symbol": "EURUSD", "barCloseTime": 2}
        assert stable_id(namespace="decision", key=k1) != stable_id(
            namespace="decision", key=k2
        )
        assert stable_id(namespace="decision", key=k1) != stable_id(
            namespace="order", key=k1
        )
