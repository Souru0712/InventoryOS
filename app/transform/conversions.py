"""Turning any entered quantity into an item's base unit, or refusing it (T1.15). Pure.

INV-FR-11..13, DR-10, DR-11: a quantity is entered in a built-in unit or in one of the item's
packages (N × another unit or package). Within a dimension the constants in app/units.py
convert exactly; across dimensions only the item's own conversion may ("1 lb → 340 ml"), never
an assumed density. Anything else is refused with a message naming what is missing. Results are
exact decimals to four places.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.units import UNITS, Dimension, UnitError

PLACES = Decimal("0.0001")
MAX_NESTING = 5  # a case of boxes of bags of ... is deep enough; anything deeper is a loop


@dataclass(frozen=True)
class Package:
    """name = quantity × unit, where unit is a built-in unit or another package's name."""

    name: str
    quantity: Decimal
    unit: str


@dataclass(frozen=True)
class Conversion:
    """from_quantity from_unit = to_quantity to_unit, across two dimensions."""

    from_quantity: Decimal
    from_unit: str
    to_quantity: Decimal
    to_unit: str

    def dimensions(self) -> frozenset[Dimension]:
        return frozenset({UNITS[self.from_unit].dimension, UNITS[self.to_unit].dimension})


class ConversionMissing(UnitError):
    """A quantity in another dimension, and the item has no conversion for it (DR-11)."""


def check_package(package: Package, packages: Mapping[str, Package]) -> None:
    """A package that can be resolved: a positive amount of a unit or package that exists."""
    if package.quantity <= 0:
        raise UnitError("A package holds more than nothing.")
    if package.name.strip().casefold() in {code.casefold() for code in UNITS}:
        raise UnitError(f"{package.name} is already a unit; pick another name.")
    if package.unit not in UNITS and package.unit.casefold() not in packages:
        raise UnitError(f"{package.unit} is not a unit or one of this item's packages.")
    _expand(Decimal("1"), package.name, {**packages, package.name.casefold(): package}, 0)


def check_conversion(conversion: Conversion) -> None:
    for unit in (conversion.from_unit, conversion.to_unit):
        if unit not in UNITS:
            raise UnitError(f"{unit} is not a unit.")
    if len(conversion.dimensions()) != 2:
        raise UnitError("A conversion links two kinds of measure, like weight and volume.")
    if conversion.from_quantity <= 0 or conversion.to_quantity <= 0:
        raise UnitError("Both sides of a conversion are amounts above zero.")


def _expand(
    quantity: Decimal, unit: str, packages: Mapping[str, Package], depth: int
) -> tuple[Decimal, Dimension]:
    """quantity of unit (a package or built-in) as base-unit amount of its own dimension."""
    package = packages.get(unit.strip().casefold())
    if package is not None:
        if depth >= MAX_NESTING:
            raise UnitError(f"The package {package.name} is defined in terms of itself.")
        return _expand(quantity * package.quantity, package.unit, packages, depth + 1)
    known = UNITS.get(unit.strip())
    if known is None:
        raise UnitError(f"{unit} is not a unit or one of this item's packages.")
    return quantity * known.to_base, known.dimension


def to_base(
    quantity: Decimal,
    unit: str,
    *,
    dimension: Dimension,
    packages: Mapping[str, Package] | None = None,
    conversions: Sequence[Conversion] = (),
    item: str = "this item",
) -> Decimal:
    """quantity of unit in the item's base unit (g, ml or each), to four places."""
    amount, measured = _expand(quantity, unit, packages or {}, 0)
    if measured is not dimension:
        link = next((c for c in conversions if c.dimensions() == {measured, dimension}), None)
        if link is None:
            raise ConversionMissing(
                f"{item} has no conversion between {measured.value} and {dimension.value}. "
                f"Add one on the item page, like 1 lb = 340 ml."
            )
        one = link.from_quantity * UNITS[link.from_unit].to_base
        other = link.to_quantity * UNITS[link.to_unit].to_base
        if UNITS[link.from_unit].dimension is measured:
            amount = amount * other / one
        else:
            amount = amount * one / other
    return amount.quantize(PLACES)
