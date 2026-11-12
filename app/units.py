"""Unit system: count / weight / volume with lossless base-unit storage."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class Dimension(str, Enum):
    COUNT = "count"
    WEIGHT = "weight"
    VOLUME = "volume"


BASE_UNIT: dict[Dimension, str] = {
    Dimension.COUNT: "each",
    Dimension.WEIGHT: "g",
    Dimension.VOLUME: "ml",
}


@dataclass(frozen=True)
class Unit:
    code: str
    label: str
    plural: str
    dimension: Dimension
    to_base: Decimal
    step: Decimal  # display precision


def _u(code, label, plural, dim, to_base, step) -> Unit:
    return Unit(code, label, plural, dim, Decimal(to_base), Decimal(step))


UNITS: dict[str, Unit] = {
    u.code: u
    for u in [
        _u("each", "each", "each", Dimension.COUNT, "1", "1"),
        _u("dozen", "dozen", "dozen", Dimension.COUNT, "12", "0.01"),
        _u("g", "g", "g", Dimension.WEIGHT, "1", "1"),
        _u("kg", "kg", "kg", Dimension.WEIGHT, "1000", "0.001"),
        _u("oz", "oz", "oz", Dimension.WEIGHT, "28.349523125", "0.01"),
        _u("lb", "lb", "lb", Dimension.WEIGHT, "453.59237", "0.01"),
        _u("ml", "ml", "ml", Dimension.VOLUME, "1", "1"),
        _u("l", "L", "L", Dimension.VOLUME, "1000", "0.001"),
        _u("floz", "fl oz", "fl oz", Dimension.VOLUME, "29.5735295625", "0.01"),
        _u("tbsp", "tbsp", "tbsp", Dimension.VOLUME, "14.78676478125", "0.01"),
        _u("tsp", "tsp", "tsp", Dimension.VOLUME, "4.92892159375", "0.01"),
        _u("cup", "cup", "cups", Dimension.VOLUME, "236.5882365", "0.01"),
        _u("pt", "pt", "pt", Dimension.VOLUME, "473.176473", "0.01"),
        _u("qt", "qt", "qt", Dimension.VOLUME, "946.352946", "0.01"),
        _u("gal", "gal", "gal", Dimension.VOLUME, "3785.411784", "0.001"),
    ]
}

UNITS_BY_DIMENSION: dict[Dimension, list[Unit]] = {d: [] for d in Dimension}
for _unit in UNITS.values():
    UNITS_BY_DIMENSION[_unit.dimension].append(_unit)


class UnitError(ValueError):
    """Raised on unknown units or cross-dimension conversion."""


def get_unit(code: str) -> Unit:
    try:
        return UNITS[code]
    except KeyError:
        raise UnitError(f"unknown unit {code!r}; known: {sorted(UNITS)}") from None


def to_base(qty: Decimal | float | str, unit_code: str) -> Decimal:
    """Convert a display quantity into its dimension's base unit."""
    return (Decimal(str(qty)) * get_unit(unit_code).to_base).quantize(Decimal("0.0001"))


def from_base(qty_base: Decimal | float | str, unit_code: str) -> Decimal:
    """Convert a base-unit quantity into a display unit."""
    unit = get_unit(unit_code)
    value = Decimal(str(qty_base)) / unit.to_base
    return value.quantize(unit.step, rounding=ROUND_HALF_UP)


def convert(qty: Decimal | float | str, from_code: str, to_code: str) -> Decimal:
    """Convert between units of the same dimension, keeping full working precision."""
    src, dst = get_unit(from_code), get_unit(to_code)
    if src.dimension is not dst.dimension:
        raise UnitError(
            f"cannot convert {src.dimension.value} ({src.code}) to {dst.dimension.value} ({dst.code})"
        )
    return (to_base(qty, from_code) / dst.to_base).quantize(Decimal("0.0001"))


def format_qty(qty_base: Decimal | float | str, unit_code: str) -> str:
    """Human-readable quantity, e.g. '12.5 kg'."""
    unit = get_unit(unit_code)
    text = format(from_base(qty_base, unit_code), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text or '0'} {unit.label}"


def format_approx(qty_base: Decimal | float | str, unit_code: str) -> str:
    """format_qty for estimates: whole units for counts, one decimal for weight and volume."""
    step = Decimal("1") if dimension_of(unit_code) is Dimension.COUNT else Decimal("0.1")
    shown = from_base(qty_base, unit_code).quantize(step, rounding=ROUND_HALF_UP)
    return format_qty(to_base(shown, unit_code), unit_code)


def format_amount(qty_base: Decimal | float | str, unit_code: str, approx: bool = False) -> str:
    """A quantity to put inside a sentence: '72 on the shelf', never '72 each on the shelf'.

    'each' labels a quantity column well, but after a number in running text it is not English,
    so the bare count stands in for it. Every other unit reads naturally: '5.2 L left'.
    """
    text = format_approx(qty_base, unit_code) if approx else format_qty(qty_base, unit_code)
    suffix = f" {get_unit(unit_code).label}"
    if unit_code == "each" and text.endswith(suffix):
        return text[: -len(suffix)]
    return text


def format_change(qty_base: Decimal | float | str, unit_code: str) -> str:
    """A signed change to put inside a sentence: '+2 kg', '-1.5 L', '+3'."""
    value = Decimal(str(qty_base))
    sign = "+" if value > 0 else "-" if value < 0 else ""
    return sign + format_amount(abs(value), unit_code)


def dimension_of(unit_code: str) -> Dimension:
    return get_unit(unit_code).dimension


def assert_same_dimension(unit_code: str, dimension: Dimension) -> None:
    actual = dimension_of(unit_code)
    if actual is not dimension:
        raise UnitError(f"unit {unit_code!r} is {actual.value}, expected {dimension.value}")
