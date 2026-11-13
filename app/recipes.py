"""Versioned recipes (T2.02; RCP-FR-02, -04..09, -14, DR-11, DR-16, DR-17, ADR-0051).

The only writer of recipes, their versions and lines. A recipe belongs to one owner: a product
variation, an "add" modifier, or a prepared item (its production recipe). Saving never edits:
it adds the next version, effective now or at a chosen later moment, and anything that happened
earlier keeps the version it used (DR-16). Every line's unit must convert for its item (DR-11),
and a prepared item can never end up using itself (DR-17). Saves are recorded in the business
history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.audit import record_audit
from app.items import entered_base
from app.models import (
    InventoryItem,
    Modifier,
    ProductVariation,
    Recipe,
    RecipeComponent,
    RecipeLine,
    RecipeVersion,
    StockItem,
)
from app.transform.items import ItemRuleError
from app.transform.recipes import (
    RECIPE_ITEM_TYPES,
    SERVING,
    Line,
    RecipeRuleError,
    check_effective_from,
    check_lines,
    check_quantity,
    find_cycle,
    same_recipe,
    scale_lines,
    size_ratio,
    version_at,
)

log = logging.getLogger(__name__)
FROM_THE_START = datetime(2000, 1, 1, tzinfo=UTC)  # what migrated recipes start from


class NotFound(LookupError):
    """No such recipe owner, recipe or item that org may see."""


@dataclass(frozen=True)
class Owner:
    """Whose recipe it is: kind is "variation", "modifier" or "item"."""

    kind: str
    id: int


@dataclass(frozen=True)
class NewLine:
    inventory_item_id: int
    quantity: str
    unit: str = ""
    packaging: bool | None = None  # None: packaging when the item is a packaging item


# --- owners --------------------------------------------------------------------------------------


def _owner_row(
    db: Session, org: OrgContext, owner: Owner
) -> ProductVariation | Modifier | InventoryItem:
    row: ProductVariation | Modifier | InventoryItem | None = None
    if owner.kind == "variation":
        row = get_scoped(db, org, ProductVariation, owner.id)
    elif owner.kind == "modifier":
        modifier = get_scoped(db, org, Modifier, owner.id)
        if modifier is not None and modifier.effect != "add":
            raise RecipeRuleError("Only a modifier that adds ingredients has a recipe.")
        row = modifier
    elif owner.kind == "item":
        item = get_scoped(db, org, InventoryItem, owner.id)
        if item is not None and item.item_type not in RECIPE_ITEM_TYPES:
            raise RecipeRuleError("Only a prepared or finished item has a production recipe.")
        row = item
    if row is None:
        raise NotFound(f"{owner.kind} {owner.id}")
    return row


def owner_name(db: Session, org: OrgContext, owner: Owner) -> str:
    row = _owner_row(db, org, owner)
    if isinstance(row, ProductVariation):
        return f"{row.product.name} {row.name}"
    return row.name


def _column(owner: Owner):
    return {
        "variation": Recipe.product_variation_id,
        "modifier": Recipe.modifier_id,
        "item": Recipe.inventory_item_id,
    }[owner.kind]


def recipe_of(db: Session, org: OrgContext, owner: Owner) -> Recipe | None:
    return db.scalars(select_scoped(org, Recipe).where(_column(owner) == owner.id)).first()


# --- reading -------------------------------------------------------------------------------------


def versions(db: Session, org: OrgContext, recipe: Recipe) -> list[RecipeVersion]:
    """Every version, newest first (RCP-FR-08)."""
    rows = db.scalars(select_scoped(org, RecipeVersion).where(RecipeVersion.recipe_id == recipe.id))
    return sorted(rows, key=lambda v: v.version_no, reverse=True)


def effective_version(
    db: Session, org: OrgContext, recipe: Recipe, at: datetime | None = None
) -> RecipeVersion | None:
    """The version in effect at an instant (now by default): what a sale, waste or production
    at that moment uses, however late it is recorded (DR-16, AC-RCP-02)."""
    rows = versions(db, org, recipe)
    number = version_at(((v.version_no, v.effective_from) for v in rows), at or datetime.now(UTC))
    return next((v for v in rows if v.version_no == number), None)


def _lines_of(version: RecipeVersion) -> list[Line]:
    return [
        Line(
            ln.inventory_item_id,
            Decimal(ln.quantity),
            ln.unit,
            Decimal(ln.qty_base),
            ln.is_packaging,
        )
        for ln in version.lines
    ]


def _uses(db: Session, org: OrgContext, now: datetime) -> dict[int, list[int]]:
    """Each item with a production recipe, and the items its current version uses."""
    result: dict[int, list[int]] = {}
    for recipe in db.scalars(
        select_scoped(org, Recipe).where(Recipe.inventory_item_id.is_not(None))
    ):
        current = effective_version(db, org, recipe, now)
        later = [v for v in recipe.versions if v.effective_from > now]
        used = {ln.inventory_item_id for v in [current, *later] if v for ln in v.lines}
        if recipe.inventory_item_id is not None:
            result[recipe.inventory_item_id] = sorted(used)
    return result


# --- saving --------------------------------------------------------------------------------------


def save_recipe(
    db: Session,
    org: OrgContext,
    owner: Owner,
    lines: list[NewLine],
    *,
    yield_quantity: str = "1",
    yield_unit: str = "",
    instructions: str = "",
    effective_from: datetime | None = None,
    ip: str | None = None,
) -> RecipeVersion | None:
    """Add the next version of the owner's recipe; None when nothing changed (RCP-FR-07)."""
    now = datetime.now(UTC)
    target = _owner_row(db, org, owner)
    starts = check_effective_from(effective_from, now)
    items: dict[int, InventoryItem] = {}
    checked: list[Line] = []
    for new in lines:
        item = get_scoped(db, org, InventoryItem, new.inventory_item_id)
        if item is None:
            raise NotFound(f"item {new.inventory_item_id}")
        if not item.is_active:
            raise RecipeRuleError(f"{item.name} is inactive; reactivate it to use it.")
        items[item.id] = item
        amount = check_quantity(new.quantity, f"The amount of {item.name}")
        unit = (new.unit or item.display_unit).strip()
        try:
            base = entered_base(item, str(amount), unit)  # DR-11: refused, naming what is missing
        except ItemRuleError as exc:
            raise RecipeRuleError(str(exc)) from exc
        packaging = item.item_type == "packaging" if new.packaging is None else new.packaging
        checked.append(Line(item.id, amount, unit, base, packaging))
    names = {i: item.name for i, item in items.items()}
    checked = check_lines(checked, names)
    if owner.kind == "item":
        path = find_cycle(owner.id, _uses(db, org, now), [ln.inventory_item_id for ln in checked])
        if path:
            known = {**names, owner.id: target.name}
            for item in db.scalars(
                select_scoped(org, InventoryItem).where(InventoryItem.id.in_(path))
            ):
                known.setdefault(item.id, item.name)
            chain = " uses ".join(known.get(i, "?") for i in path)
            raise RecipeRuleError(f"That would go round in a circle: {chain}.")
    amount_made, unit_made, base_made = _yield(target, owner, yield_quantity, yield_unit)
    text = instructions.strip() or None

    recipe = recipe_of(db, org, owner)
    if recipe is None:
        recipe = Recipe(tenant_id=org.tenant_id, **{_column(owner).key: owner.id})
        db.add(recipe)
        db.flush()
    latest = versions(db, org, recipe)
    if latest and same_recipe(
        _lines_of(latest[0]),
        checked,
        (Decimal(latest[0].yield_quantity), latest[0].yield_unit, latest[0].instructions or ""),
        (amount_made, unit_made, text or ""),
    ):
        return None
    number = (latest[0].version_no if latest else 0) + 1
    version = RecipeVersion(
        tenant_id=org.tenant_id,
        recipe_id=recipe.id,
        version_no=number,
        effective_from=starts,
        yield_quantity=amount_made,
        yield_unit=unit_made,
        yield_base=base_made,
        instructions=text,
        created_by=org.user_id,
    )
    db.add(version)
    db.flush()
    for position, line in enumerate(checked):
        db.add(
            RecipeLine(
                tenant_id=org.tenant_id,
                recipe_version_id=version.id,
                position=position,
                inventory_item_id=line.inventory_item_id,
                quantity=line.quantity,
                unit=line.unit,
                qty_base=line.qty_base,
                is_packaging=line.is_packaging,
            )
        )
    db.flush()
    db.refresh(recipe)
    name = owner_name(db, org, owner)
    record_audit(
        db,
        org,
        "recipe.saved",
        f"Saved version {number} of the recipe for {name}"
        + ("" if starts == now else f", effective {starts:%d %b %Y %H:%M} UTC"),
        subject_type="recipe",
        subject_id=recipe.id,
        after={
            "version": number,
            "lines": [
                {"item": names[ln.inventory_item_id], "quantity": str(ln.quantity), "unit": ln.unit}
                for ln in checked
            ],
        },
        ip=ip,
    )
    log.info("recipe %s version %s saved with %d lines", recipe.id, number, len(checked))
    return version


def _yield(
    target: ProductVariation | Modifier | InventoryItem, owner: Owner, quantity: str, unit: str
) -> tuple[Decimal, str, Decimal]:
    """What one making yields (RCP-FR-05): servings for a product or modifier, an amount of the
    prepared item in any of its units otherwise."""
    amount = check_quantity(quantity or "1", "The yield")
    if not isinstance(target, InventoryItem):
        return amount, SERVING, amount
    chosen = (unit or target.display_unit).strip()
    try:
        return amount, chosen, entered_base(target, str(amount), chosen)
    except ItemRuleError as exc:
        raise RecipeRuleError(str(exc)) from exc


# --- start from another size ---------------------------------------------------------------------


def start_from(
    db: Session, org: OrgContext, from_variation_id: int, to_variation_id: int
) -> list[NewLine]:
    """Another size's current recipe, scaled by size for editing (RCP-FR-09, AC-RCP-06):
    "start from 12 oz" fills a 16 oz recipe at 16/12 of each line."""
    source = _owner_row(db, org, Owner("variation", from_variation_id))
    target = _owner_row(db, org, Owner("variation", to_variation_id))
    recipe = recipe_of(db, org, Owner("variation", source.id))
    current = effective_version(db, org, recipe) if recipe else None
    if current is None:
        raise RecipeRuleError(f"{source.name} has no recipe to start from.")
    scaled = scale_lines(_lines_of(current), size_ratio(source.name, target.name))
    return [
        NewLine(ln.inventory_item_id, f"{ln.quantity.normalize():f}", ln.unit, ln.is_packaging)
        for ln in scaled
    ]


# --- version 1 from the old recipe components ----------------------------------------------------


def recipes_from_components(db: Session, org: OrgContext) -> int:
    """Version 1, from the start of time, for variations whose sold item has components and
    no recipe yet (DM-9's rule, as the migration applies it): the number made. Used by the
    demo seed after the catalog exists."""
    by_sold = {
        v.source_sold_item_id: v
        for v in db.scalars(select_scoped(org, ProductVariation))
        if v.source_sold_item_id is not None
    }
    have = {
        r.product_variation_id
        for r in db.scalars(select_scoped(org, Recipe))
        if r.product_variation_id is not None
    }
    stock = {s.id: s for s in db.scalars(select_scoped(org, StockItem))}
    wanted: dict[int, dict[int, tuple[Decimal, InventoryItem]]] = {}
    for component in sorted(db.scalars(select_scoped(org, RecipeComponent)), key=lambda c: c.id):
        variation = by_sold.get(component.sold_item_id)
        place = stock.get(component.stock_item_id)
        if variation is None or variation.id in have or place is None:
            continue
        if place.inventory_item is None:
            continue
        lines = wanted.setdefault(variation.id, {})
        lines.setdefault(
            place.inventory_item.id, (Decimal(component.qty_base_per_sale), place.inventory_item)
        )
    for variation_id, lines in wanted.items():
        recipe = Recipe(tenant_id=org.tenant_id, product_variation_id=variation_id)
        db.add(recipe)
        db.flush()
        version = RecipeVersion(
            tenant_id=org.tenant_id,
            recipe_id=recipe.id,
            version_no=1,
            effective_from=FROM_THE_START,
            yield_quantity=Decimal(1),
            yield_unit=SERVING,
            yield_base=Decimal(1),
        )
        db.add(version)
        db.flush()
        for position, (qty, item) in enumerate(lines.values()):
            amount = qty.quantize(Decimal("0.0001"))
            db.add(
                RecipeLine(
                    tenant_id=org.tenant_id,
                    recipe_version_id=version.id,
                    position=position,
                    inventory_item_id=item.id,
                    quantity=amount,
                    unit=item.base_unit,
                    qty_base=amount,
                    is_packaging=item.item_type == "packaging",
                )
            )
    db.flush()
    return len(wanted)
