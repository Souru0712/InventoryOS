"""The owned product catalog's rules (T2.01; RCP-FR-01, -03, C-36). Pure.

InventoryOS owns what a business sells: products, each sold in variations (sizes) with an
informational price, and modifiers with an inventory effect. A POS catalog is mapped onto this
catalog later (T3.03, T3.04), never the reverse.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

# RCP-FR-03 in R1: "none" changes nothing, "scale" multiplies the base recipe, "add" adds the
# modifier's own lines (its recipe, T2.02). "remove" and "substitute" arrive in R1.1.
MODIFIER_EFFECTS = ("none", "scale", "add")
EFFECT_LABELS = {
    "none": "No effect on stock",
    "scale": "Scales the recipe",
    "add": "Adds its own ingredients",
}
DEFAULT_VARIATION = "Regular"
NAME_LIMIT = 200
VARIATION_LIMIT = 100


class ProductRuleError(ValueError):
    """A catalog change the rules refuse; nothing is written."""


def clean_label(raw: str, limit: int, what: str = "it") -> str:
    """A name as stored: single spaces, trimmed, refused when blank."""
    name = " ".join((raw or "").split())[:limit]
    if not name:
        raise ProductRuleError(f"Give {what} a name.")
    return name


def check_price(raw: str) -> int | None:
    """An informational price in whole cents (the POS is the price authority); blank is none."""
    text = (raw or "").strip().lstrip("$").replace(",", "")
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ProductRuleError("A price is an amount like 4.50.") from exc
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
        raise ProductRuleError("A price is an amount like 4.50.")
    return int(amount * 100)


def check_modifier(effect: str, scale_factor: str) -> Decimal | None:
    """The scale factor a modifier keeps: required above zero for "scale", none otherwise."""
    if effect not in MODIFIER_EFFECTS:
        raise ProductRuleError("Choose what the modifier does to stock.")
    text = (scale_factor or "").strip()
    if effect != "scale":
        return None
    try:
        factor = Decimal(text)
    except InvalidOperation as exc:
        raise ProductRuleError("A scale is a number like 1.15.") from exc
    if not factor.is_finite() or factor <= 0:
        raise ProductRuleError("A scale is a number above zero, like 1.15.")
    return factor.quantize(Decimal("0.0001"))


def split_sold_name(name: str) -> tuple[str, str]:
    """A POS sale name as (product, variation). Square names a variation "Item - Variation"
    (`square_normalize`); a name without one is a single "Regular" variation."""
    clean = " ".join((name or "").split())
    product, sep, variation = clean.rpartition(" - ")
    if sep and product and variation:
        return product[:NAME_LIMIT], variation[:VARIATION_LIMIT]
    return clean[:NAME_LIMIT], DEFAULT_VARIATION


@dataclass
class Planned:
    """One product the catalog gains from sold items, with its variations in order."""

    name: str
    category: str | None
    active: bool
    variations: list[tuple[int, str, int | None, bool]] = field(default_factory=list)
    # (sold item id, variation name, price in cents, active)


def plan_from_sold(
    sold: Iterable[tuple[int, str, str | None, int, bool]], taken: Iterable[str] = ()
) -> list[Planned]:
    """Products and variations for sold items (id, name, category, price, active), grouped by
    product name whatever its case. Names already in the catalog (`taken`) are not reused; a
    variation name that repeats within a product gets a number."""
    existing = {name.casefold() for name in taken}
    plans: dict[str, Planned] = {}
    for sold_id, name, category, price, active in sorted(sold, key=lambda row: row[0]):
        product_name, variation = split_sold_name(name)
        key = product_name.casefold()
        if key in existing:
            continue
        plan = plans.setdefault(key, Planned(product_name, category or None, False))
        plan.active = plan.active or active
        names = {v[1].casefold() for v in plan.variations}
        unique, n = variation, 2
        while unique.casefold() in names:
            unique, n = f"{variation} {n}", n + 1
        plan.variations.append((sold_id, unique, price if price and price > 0 else None, active))
    return list(plans.values())
