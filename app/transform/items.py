"""How a per-location stock item becomes one business-wide inventory item (C-38, DM-5). Pure.

The same slug at several locations is one item, provided it is measured the same way. A slug
measured differently at another location (weight here, count there) cannot share a base unit,
so it becomes its own item with the dimension in its slug.
"""

from __future__ import annotations

from decimal import Decimal

from app.units import BASE_UNIT, Dimension


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
