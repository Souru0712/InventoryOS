from decimal import Decimal

import pytest

from app.units import (
    Dimension,
    UnitError,
    convert,
    dimension_of,
    format_amount,
    format_qty,
    from_base,
    to_base,
)


def test_count_weight_volume_round_trip():
    assert to_base(3, "each") == Decimal("3.0000")
    assert to_base("2.5", "kg") == Decimal("2500.0000")
    assert to_base("1.5", "l") == Decimal("1500.0000")
    assert from_base("2500", "kg") == Decimal("2.500")
    assert from_base("1500", "l") == Decimal("1.500")


def test_imperial_conversions_keep_working_precision():
    assert convert(1, "lb", "g") == Decimal("453.5924")
    assert convert(1, "gal", "l") == Decimal("3.7854")
    assert convert(16, "oz", "lb") == Decimal("1.0000")


def test_cross_dimension_conversion_raises():
    with pytest.raises(UnitError, match="cannot convert"):
        convert(1, "kg", "l")


def test_unknown_unit_raises():
    with pytest.raises(UnitError, match="unknown unit"):
        to_base(1, "furlong")


def test_dimensions_are_classified():
    assert dimension_of("each") is Dimension.COUNT
    assert dimension_of("lb") is Dimension.WEIGHT
    assert dimension_of("floz") is Dimension.VOLUME


def test_format_reads_like_a_shelf_label():
    assert format_qty("2500", "kg") == "2.5 kg"
    assert format_qty("12", "each") == "12 each"
    assert format_qty("750", "ml") == "750 ml"


def test_a_count_inside_a_sentence_drops_each_but_every_other_unit_stays():
    assert format_amount("72", "each") == "72"
    assert format_amount("5177", "l", approx=True) == "5.2 L"
    assert format_amount("3", "dozen") == "0.25 dozen"
    assert format_qty("72", "each") == "72 each"  # columns keep the unit
