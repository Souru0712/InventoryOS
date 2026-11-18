"""Consumption: what each sale used (T2.05; WF-12, POS-FR-07, PRD-FR-11, DR-18, ADR-0054).

This part (T2.05-a) reads: it turns a recorded sale line into the resolver's input, with the
recipe versions in effect when the sale happened, and resolves it. A sale with a time uses that
instant; a day's sales entered as a whole use the end of that business day at its location.
Posting what it used to the ledger, once per sale revision, is T2.05-b.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.models import (
    InventoryItem,
    Location,
    Modifier,
    ProductVariation,
    RecipeVersion,
    SalesTransaction,
    SalesTransactionLine,
)
from app.recipes import Owner, effective_version, recipe_of
from app.transform.business_days import business_day_end
from app.transform.consumption import LineIn, ModifierIn, RecipeIn, Resolved, resolve
from app.transform.recipes import RECIPE_ITEM_TYPES


def sale_moment(db: Session, org: OrgContext, transaction: SalesTransaction) -> datetime:
    """When a sale happened, for choosing recipe versions: its time, or the end of its business
    day at its location for a day's sales entered as a whole."""
    if transaction.occurred_at is not None:
        return transaction.occurred_at
    place = get_scoped(db, org, Location, transaction.location_id)
    zone = place.timezone if place else "UTC"
    cutoff = place.business_day_cutoff_hour if place else 0
    return business_day_end(transaction.business_date, zone, cutoff)


def _recipe_in(version: RecipeVersion | None) -> RecipeIn | None:
    if version is None:
        return None
    return RecipeIn(
        version.id,
        tuple(
            (ln.inventory_item_id, Decimal(ln.qty_base), ln.is_packaging) for ln in version.lines
        ),
        Decimal(version.yield_base),
    )


def _version_of(db: Session, org: OrgContext, owner: Owner, at: datetime) -> RecipeIn | None:
    recipe = recipe_of(db, org, owner)
    return _recipe_in(effective_version(db, org, recipe, at)) if recipe else None


def resolve_line(
    db: Session, org: OrgContext, line: SalesTransactionLine, at: datetime
) -> Resolved:
    """What one sold line used, by the recipes in effect at `at` (WF-12 steps 1 to 5)."""
    variation = (
        get_scoped(db, org, ProductVariation, line.product_variation_id)
        if line.product_variation_id is not None
        else None
    )
    if variation is None:
        return resolve(LineIn(Decimal(line.quantity), None, mapped=False))
    modifiers = []
    for chosen in line.modifiers:
        modifier = get_scoped(db, org, Modifier, chosen.modifier_id)
        if modifier is None:
            continue
        modifiers.append(
            ModifierIn(
                modifier.id,
                modifier.effect,
                Decimal(modifier.scale_factor) if modifier.scale_factor is not None else None,
                _version_of(db, org, Owner("modifier", modifier.id), at)
                if modifier.effect == "add"
                else None,
                chosen.quantity,
            )
        )
    made_in_house = frozenset(
        item.id
        for item in db.scalars(
            select_scoped(org, InventoryItem).where(InventoryItem.item_type.in_(RECIPE_ITEM_TYPES))
        )
    )
    return resolve(
        LineIn(
            Decimal(line.quantity),
            _version_of(db, org, Owner("variation", variation.id), at),
            made_to_order=variation.product.production_mode == "made_to_order",
            modifiers=tuple(modifiers),
        ),
        prepared=lambda item_id: _version_of(db, org, Owner("item", item_id), at),
        prepared_ids=made_in_house,
    )


def resolve_sale(
    db: Session, org: OrgContext, transaction: SalesTransaction
) -> dict[int, Resolved]:
    """Every line of a sale, resolved at the sale's moment: line id -> what it used."""
    at = sale_moment(db, org, transaction)
    return {line.id: resolve_line(db, org, line, at) for line in transaction.lines}
