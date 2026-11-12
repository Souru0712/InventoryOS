"""How a per-location stock item becomes one business-wide inventory item (C-38, DM-5). Pure.

The same slug at several locations is one item, provided it is measured the same way. A slug
measured differently at another location (weight here, count there) cannot share a base unit,
so it becomes its own item with the dimension in its slug.
"""

from __future__ import annotations

from decimal import Decimal

from app.units import BASE_UNIT, UNITS, Dimension

# What each item type is called on the item screens (INV-FR-02).
ITEM_TYPE_LABELS = {
    "raw": "Raw ingredient or material",
    "prepared": "Prepared item",
    "finished": "Finished good",
    "packaging": "Packaging",
    "supply": "Supply",
    "resale": "Resale good",
}


class ItemRuleError(ValueError):
    """An item or category change the rules refuse; the message is shown to the person."""


def clean_name(raw: str, limit: int = 300) -> str:
    """A name as stored: single spaces, trimmed; refused when blank."""
    name = " ".join((raw or "").split())[:limit]
    if not name:
        raise ItemRuleError("Give it a name.")
    return name


def known_item_type(value: str) -> str:
    if value not in ITEM_TYPE_LABELS:
        raise ItemRuleError("Pick what kind of item it is.")
    return value


def display_unit_for(dimension: Dimension, unit: str) -> str:
    """A display unit that measures the item's dimension."""
    if unit not in UNITS or UNITS[unit].dimension is not dimension:
        raise ItemRuleError(f"{unit or 'That'} is not a {dimension.value} unit.")
    return unit


def check_dimension_change(current: Dimension, wanted: Dimension, has_history: bool) -> None:
    """INV-FR-14: once stock has moved, the base unit stays; make a new item instead."""
    if wanted is not current and has_history:
        raise ItemRuleError(
            "This item already has stock history, so how it is measured cannot change. "
            "Create a new item instead."
        )


def check_levels(reorder: Decimal, par: Decimal | None, minimum: Decimal | None) -> None:
    """Levels per location (INV-FR-06): none negative; par, when set, at or above the others."""
    levels = [level for level in (reorder, par, minimum) if level is not None]
    if any(level < 0 for level in levels):
        raise ItemRuleError("Levels cannot be negative.")
    if par is not None and (par < reorder or (minimum is not None and par < minimum)):
        raise ItemRuleError("Par is the most you keep: set it at or above the other levels.")


def unique_slug(base: str, taken: set[str]) -> str:
    """base, or base-2, base-3 ... whichever is free."""
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}"[:160] in taken:
        n += 1
    return f"{base}-{n}"[:160]


def item_slug(slug: str, dimension: Dimension, existing: Dimension | None) -> str:
    """The inventory item slug for a stock item, given the dimension of an item already using
    that slug in the business (None when there is none)."""
    if existing is None or existing == dimension:
        return slug
    return f"{slug}-{dimension.value}"[:160]


def item_type(retail_per_base_cents: Decimal | None) -> str:
    """Something with a shelf price is sold as it is; everything else is an ingredient until
    someone says otherwise (the item screens in T1.14)."""
    return "resale" if retail_per_base_cents is not None else "raw"


def base_unit(dimension: Dimension) -> str:
    return BASE_UNIT[dimension]
