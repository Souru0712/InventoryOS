"""The owned product catalog (T2.01; RCP-FR-01, -03, C-36, ADR-0050).

Products, their variations and modifiers belong to the business and work with no POS: a
Standalone business defines what it sells here, and a POS catalog is mapped onto these rows later
(T3.03, T3.04). Nothing is deleted: products, variations and modifiers are deactivated (DR-24).
Every change is recorded in the business history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.audit import changes, record_audit
from app.models import Modifier, Product, ProductVariation, SoldItem
from app.transform.products import (
    NAME_LIMIT,
    VARIATION_LIMIT,
    ProductRuleError,
    check_modifier,
    check_price,
    clean_label,
    plan_from_sold,
)

log = logging.getLogger(__name__)


class NotFound(LookupError):
    """No such product, variation or modifier that org may see."""


@dataclass(frozen=True)
class NewVariation:
    name: str
    price: str = ""


# --- reading -------------------------------------------------------------------------------------


def list_products(db: Session, org: OrgContext, *, inactive: bool = False) -> list[Product]:
    query = select_scoped(org, Product)
    if not inactive:
        query = query.where(Product.is_active.is_(True))
    return sorted(db.scalars(query), key=lambda p: (p.category or "~", p.name.casefold()))


def get_product(db: Session, org: OrgContext, product_id: int) -> Product:
    product = get_scoped(db, org, Product, product_id)
    if product is None:
        raise NotFound(product_id)
    return product


def list_modifiers(db: Session, org: OrgContext) -> list[Modifier]:
    rows = db.scalars(select_scoped(org, Modifier))
    return sorted(rows, key=lambda m: (not m.is_active, m.group_name or "~", m.name.casefold()))


def categories(db: Session, org: OrgContext) -> list[str]:
    """Categories already in use, to suggest when naming a product's."""
    return sorted({p.category for p in db.scalars(select_scoped(org, Product)) if p.category})


# --- products ------------------------------------------------------------------------------------


def _product_taken(db: Session, org: OrgContext, name: str, other_than: int | None = None) -> bool:
    wanted = name.casefold()
    return any(
        p.name.casefold() == wanted and p.id != other_than
        for p in db.scalars(select_scoped(org, Product))
    )


def _category(raw: str) -> str | None:
    return " ".join((raw or "").split())[:NAME_LIMIT] or None


def create_product(
    db: Session,
    org: OrgContext,
    *,
    name: str,
    category: str = "",
    variations: list[NewVariation],
    ip: str | None = None,
) -> Product:
    """A product and its variations; with none named it is sold as one "Regular" variation."""
    name = clean_label(name, NAME_LIMIT, "the product")
    if _product_taken(db, org, name):
        raise ProductRuleError(f"There is already a product called {name}.")
    wanted = [v for v in variations if v.name.strip() or v.price.strip()] or [
        NewVariation("Regular")
    ]
    planned: list[tuple[str, int | None]] = []
    for variation in wanted:
        label = clean_label(variation.name, VARIATION_LIMIT, "each size")
        if label.casefold() in {p[0].casefold() for p in planned}:
            raise ProductRuleError(f"{label} is listed twice.")
        planned.append((label, check_price(variation.price)))
    product = Product(tenant_id=org.tenant_id, name=name, category=_category(category))
    db.add(product)
    db.flush()
    for label, price in planned:
        db.add(
            ProductVariation(
                tenant_id=org.tenant_id, product_id=product.id, name=label, price_cents=price
            )
        )
    db.flush()
    record_audit(
        db,
        org,
        "product.created",
        f"Added the product {name} ({', '.join(label for label, _ in planned)})",
        subject_type="product",
        subject_id=product.id,
        after={"name": name, "category": product.category, "variations": [p[0] for p in planned]},
        ip=ip,
    )
    log.info("product %s created with %d variations", product.id, len(planned))
    return product


def update_product(
    db: Session,
    org: OrgContext,
    product_id: int,
    *,
    name: str,
    category: str,
    ip: str | None = None,
) -> Product:
    product = get_product(db, org, product_id)
    name = clean_label(name, NAME_LIMIT, "the product")
    if _product_taken(db, org, name, other_than=product.id):
        raise ProductRuleError(f"There is already a product called {name}.")
    was = {"name": product.name, "category": product.category}
    product.name, product.category = name, _category(category)
    db.flush()
    before, after = changes(was, {"name": product.name, "category": product.category})
    if after:
        record_audit(
            db,
            org,
            "product.updated",
            f"Changed {', '.join(after)} for {product.name}",
            subject_type="product",
            subject_id=product.id,
            before=before,
            after=after,
            ip=ip,
        )
    return product


def set_product_active(
    db: Session, org: OrgContext, product_id: int, active: bool, ip: str | None = None
) -> Product:
    product = get_product(db, org, product_id)
    if product.is_active != active:
        product.is_active = active
        db.flush()
        record_audit(
            db,
            org,
            "product.reactivated" if active else "product.deactivated",
            f"{'Reactivated' if active else 'Deactivated'} the product {product.name}",
            subject_type="product",
            subject_id=product.id,
            ip=ip,
        )
    return product


# --- variations ----------------------------------------------------------------------------------


def _variation(db: Session, org: OrgContext, product: Product, variation_id: int):
    variation = get_scoped(db, org, ProductVariation, variation_id)
    if variation is None or variation.product_id != product.id:
        raise NotFound(variation_id)
    return variation


def _variation_taken(product: Product, name: str, other_than: int | None = None) -> bool:
    wanted = name.casefold()
    return any(v.name.casefold() == wanted and v.id != other_than for v in product.variations)


def add_variation(
    db: Session,
    org: OrgContext,
    product_id: int,
    new: NewVariation,
    ip: str | None = None,
) -> ProductVariation:
    product = get_product(db, org, product_id)
    label = clean_label(new.name, VARIATION_LIMIT, "the size")
    if _variation_taken(product, label):
        raise ProductRuleError(f"{product.name} already has {label}.")
    variation = ProductVariation(
        tenant_id=org.tenant_id,
        product_id=product.id,
        name=label,
        price_cents=check_price(new.price),
    )
    db.add(variation)
    db.flush()
    db.refresh(product)
    record_audit(
        db,
        org,
        "product.variation_added",
        f"Added {label} to {product.name}",
        subject_type="product_variation",
        subject_id=variation.id,
        after={"name": label, "price_cents": variation.price_cents},
        ip=ip,
    )
    return variation


def update_variation(
    db: Session,
    org: OrgContext,
    product_id: int,
    variation_id: int,
    *,
    name: str,
    price: str | None,
    active: bool,
    ip: str | None = None,
) -> ProductVariation:
    product = get_product(db, org, product_id)
    variation = _variation(db, org, product, variation_id)
    label = clean_label(name, VARIATION_LIMIT, "the size")
    if _variation_taken(product, label, other_than=variation.id):
        raise ProductRuleError(f"{product.name} already has {label}.")
    cents = variation.price_cents if price is None else check_price(price)
    if (
        not active
        and variation.is_active
        and not any(v.is_active for v in product.variations if v.id != variation.id)
    ):
        raise ProductRuleError(
            f"{product.name} needs one size on sale; deactivate the product instead."
        )
    was = _variation_state(variation)
    variation.name, variation.price_cents, variation.is_active = label, cents, active
    db.flush()
    before, after = changes(was, _variation_state(variation))
    if after:
        record_audit(
            db,
            org,
            "product.variation_updated",
            f"Changed {', '.join(after)} for {product.name} {variation.name}",
            subject_type="product_variation",
            subject_id=variation.id,
            before=before,
            after=after,
            ip=ip,
        )
    return variation


def _variation_state(variation: ProductVariation) -> dict:
    return {
        "name": variation.name,
        "price_cents": variation.price_cents,
        "active": variation.is_active,
    }


# --- modifiers -----------------------------------------------------------------------------------


def get_modifier(db: Session, org: OrgContext, modifier_id: int) -> Modifier:
    modifier = get_scoped(db, org, Modifier, modifier_id)
    if modifier is None:
        raise NotFound(modifier_id)
    return modifier


def _modifier_taken(db: Session, org: OrgContext, name: str, other_than: int | None) -> bool:
    wanted = name.casefold()
    return any(
        m.name.casefold() == wanted and m.id != other_than
        for m in db.scalars(select_scoped(org, Modifier))
    )


def _modifier_state(modifier: Modifier) -> dict:
    return {
        "name": modifier.name,
        "group": modifier.group_name,
        "effect": modifier.effect,
        "scale_factor": str(modifier.scale_factor) if modifier.scale_factor is not None else None,
        "active": modifier.is_active,
    }


def save_modifier(
    db: Session,
    org: OrgContext,
    *,
    name: str,
    group: str,
    effect: str,
    scale_factor: str,
    active: bool = True,
    modifier_id: int | None = None,
    ip: str | None = None,
) -> Modifier:
    """Create a modifier, or change one (RCP-FR-03: none, scale, add in R1)."""
    label = clean_label(name, VARIATION_LIMIT, "the modifier")
    if _modifier_taken(db, org, label, other_than=modifier_id):
        raise ProductRuleError(f"There is already a modifier called {label}.")
    factor = check_modifier(effect, scale_factor)
    if modifier_id is None:
        modifier = Modifier(tenant_id=org.tenant_id)
        db.add(modifier)
        was: dict = {}
    else:
        modifier = get_modifier(db, org, modifier_id)
        was = _modifier_state(modifier)
    modifier.name = label
    modifier.group_name = _category(group)
    modifier.effect, modifier.scale_factor, modifier.is_active = effect, factor, active
    db.flush()
    if modifier_id is None:
        record_audit(
            db,
            org,
            "product.modifier_created",
            f"Added the modifier {label}",
            subject_type="modifier",
            subject_id=modifier.id,
            after=_modifier_state(modifier),
            ip=ip,
        )
        return modifier
    before, after = changes(was, _modifier_state(modifier))
    if after:
        record_audit(
            db,
            org,
            "product.modifier_updated",
            f"Changed {', '.join(after)} for the modifier {label}",
            subject_type="modifier",
            subject_id=modifier.id,
            before=before,
            after=after,
            ip=ip,
        )
    return modifier


# --- from the POS sale items ---------------------------------------------------------------------


def products_from_sold_items(db: Session, org: OrgContext) -> int:
    """Owned products and variations for sale items that have none yet (DM-9's rule, as the
    migration applies it): the number of variations made. Used by the demo seed."""
    linked = {
        v.source_sold_item_id
        for v in db.scalars(select_scoped(org, ProductVariation))
        if v.source_sold_item_id is not None
    }
    sold = [
        (s.id, s.name, s.category, s.price_cents, s.is_active)
        for s in db.scalars(select_scoped(org, SoldItem))
        if s.id not in linked
    ]
    taken = [p.name for p in db.scalars(select_scoped(org, Product))]
    made = 0
    for plan in plan_from_sold(sold, taken):
        product = Product(
            tenant_id=org.tenant_id, name=plan.name, category=plan.category, is_active=plan.active
        )
        db.add(product)
        db.flush()
        for sold_id, label, price, active in plan.variations:
            db.add(
                ProductVariation(
                    tenant_id=org.tenant_id,
                    product_id=product.id,
                    name=label,
                    price_cents=price,
                    is_active=active,
                    source_sold_item_id=sold_id,
                )
            )
            made += 1
    db.flush()
    return made
