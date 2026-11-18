"""Recipe costing, margins and completeness (T2.03; RCP-FR-10..13, -17, AC-RCP-03, -07, -08,
ADR-0052).

Read-only. Costs are at one location, because each item's cost basis is (ADR-0048): a business
with one location sees its own figures, one with several picks a location. A prepared item with
no cost of its own is costed from its production recipe. A receipt that changes a cost shows
which recipes it touches and how their margins move.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.costs import history
from app.models import (
    InventoryItem,
    ItemCostHistory,
    Location,
    Product,
    ProductVariation,
    Recipe,
    RecipeVersion,
    SoldItem,
    StockItem,
    TrackMode,
)
from app.recipes import Owner, effective_version
from app.sales import daily_sales
from app.transform.business_days import business_date
from app.transform.costing import Margin, RecipeCost, cost_recipe, margin_of, per_base_cost
from app.transform.dashboard import DateRange
from app.transform.recipes import Line

DEPTH = 6  # prepared items inside prepared items; DR-17 already forbids circles


def costing_location(
    db: Session, org: OrgContext, location_id: int | None = None
) -> Location | None:
    """The location whose costs are shown: the one asked for, else the first active one."""
    query = select_scoped(org, Location).where(Location.is_active.is_(True))
    if location_id is not None:
        query = query.where(Location.id == location_id)
    return db.scalars(query.order_by(Location.id)).first()


def _lines(version: RecipeVersion) -> list[Line]:
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


def unit_costs(
    db: Session,
    org: OrgContext,
    location_id: int,
    item_ids: Iterable[int],
    overrides: Mapping[int, Decimal | None] | None = None,
    depth: int = 0,
) -> dict[int, Decimal | None]:
    """Cents per base unit for each item at the location: its current cost, else (for a prepared
    item) what its production recipe costs per unit made, else None."""
    wanted = set(item_ids)
    known = dict(overrides or {})
    result: dict[int, Decimal | None] = {i: known.get(i) for i in wanted}
    rows = db.scalars(
        select_scoped(org, StockItem).where(
            StockItem.location_id == location_id, StockItem.inventory_item_id.in_(wanted)
        )
    )
    for row in rows:
        if row.inventory_item_id in known or row.inventory_item_id is None:
            continue
        cost = Decimal(row.cost_per_base_cents or 0)
        result[row.inventory_item_id] = cost if cost > 0 else None
    if depth < DEPTH:
        for recipe in db.scalars(
            select_scoped(org, Recipe).where(Recipe.inventory_item_id.in_(wanted))
        ):
            item_id = recipe.inventory_item_id
            if item_id is None or result.get(item_id) is not None:
                continue
            version = effective_version(db, org, recipe)
            if version is not None:
                made = version_cost(db, org, version, location_id, overrides, depth + 1)
                result[item_id] = per_base_cost(made)
    return result


def version_cost(
    db: Session,
    org: OrgContext,
    version: RecipeVersion,
    location_id: int,
    overrides: Mapping[int, Decimal | None] | None = None,
    depth: int = 0,
) -> RecipeCost:
    lines = _lines(version)
    costs = unit_costs(
        db, org, location_id, (ln.inventory_item_id for ln in lines), overrides, depth
    )
    return cost_recipe(lines, costs, Decimal(version.yield_base))


@dataclass(frozen=True)
class SizeCost:
    variation: ProductVariation
    cost: RecipeCost | None  # None: no recipe in effect
    margin: Margin | None


def size_costs(db: Session, org: OrgContext, product: Product, location_id: int) -> list[SizeCost]:
    """Each size's recipe cost and estimated margin (RCP-FR-10, -11)."""
    recipes = {
        r.product_variation_id: r
        for r in db.scalars(
            select_scoped(org, Recipe).where(
                Recipe.product_variation_id.in_([v.id for v in product.variations])
            )
        )
    }
    result = []
    for variation in product.variations:
        recipe = recipes.get(variation.id)
        version = effective_version(db, org, recipe) if recipe else None
        cost = version_cost(db, org, version, location_id) if version else None
        margin = margin_of(variation.price_cents, cost) if cost else None
        result.append(SizeCost(variation, cost, margin))
    return result


# --- what a cost change touches (RCP-FR-13, AC-RCP-07) -------------------------------------------


def _owner_of(recipe: Recipe) -> Owner:
    if recipe.product_variation_id is not None:
        return Owner("variation", recipe.product_variation_id)
    if recipe.modifier_id is not None:
        return Owner("modifier", recipe.modifier_id)
    return Owner("item", recipe.inventory_item_id or 0)


def recipes_using(db: Session, org: OrgContext, inventory_item_id: int) -> list[Recipe]:
    """Recipes whose version in effect uses the item, directly or through prepared items."""
    current = {}
    for recipe in db.scalars(select_scoped(org, Recipe)):
        version = effective_version(db, org, recipe)
        if version is not None:
            current[recipe.id] = (recipe, {ln.inventory_item_id for ln in version.lines})
    touched = {inventory_item_id}
    found: dict[int, Recipe] = {}
    grew = True
    while grew:
        grew = False
        for recipe, used in current.values():
            if recipe.id not in found and used & touched:
                found[recipe.id] = recipe
                if recipe.inventory_item_id is not None:
                    touched.add(recipe.inventory_item_id)
                    grew = True
    return [r for r in found.values() if r.inventory_item_id != inventory_item_id]


@dataclass(frozen=True)
class Affected:
    owner: Owner
    recipe: Recipe
    before: RecipeCost
    after: RecipeCost
    price_cents: int | None

    @property
    def margin_before(self) -> Margin:
        return margin_of(self.price_cents, self.before)

    @property
    def margin_after(self) -> Margin:
        return margin_of(self.price_cents, self.after)

    def figures(self, batch: bool = False) -> tuple[int, int]:
        """Before and after in whole cents, per serving (or per batch for a prepared item),
        rounded first so the change shown is the difference of the figures shown."""
        if batch:
            then, now = self.before.total_cents, self.after.total_cents
        else:
            then, now = self.before.per_yield_cents, self.after.per_yield_cents
        return int(then.quantize(Decimal("1"))), int(now.quantize(Decimal("1")))


@dataclass(frozen=True)
class Impact:
    item: InventoryItem
    location: Location
    before: Decimal | None  # the cost before the latest change
    after: Decimal | None
    changed_at: datetime | None
    rows: list[Affected]


def impact(db: Session, org: OrgContext, inventory_item_id: int, location: Location) -> Impact:
    """The latest change to an item's cost at a location and what it did to every recipe that
    uses it: each recipe's cost before and after, and its margin (RCP-FR-13, AC-RCP-07)."""
    item = get_scoped(db, org, InventoryItem, inventory_item_id)
    if item is None:
        raise LookupError(inventory_item_id)
    entries = history(db, org, location.id, inventory_item_id)
    after = Decimal(entries[0].cost_per_base_cents) if entries else None
    before = Decimal(entries[1].cost_per_base_cents) if len(entries) > 1 else None
    prices = {v.id: v.price_cents for v in db.scalars(select_scoped(org, ProductVariation))}
    rows = []
    for recipe in recipes_using(db, org, inventory_item_id):
        version = effective_version(db, org, recipe)
        if version is None:
            continue
        then = version_cost(db, org, version, location.id, {inventory_item_id: before})
        now = version_cost(db, org, version, location.id)
        owner = _owner_of(recipe)
        price = prices.get(owner.id) if owner.kind == "variation" else None
        rows.append(Affected(owner, recipe, then, now, price))
    changed = entries[0].effective_at if entries else None
    return Impact(item, location, before, after, changed, rows)


@dataclass(frozen=True)
class CostChange:
    item: InventoryItem
    location_id: int
    before: Decimal | None
    after: Decimal
    recipes: int


def cost_changes_from(db: Session, org: OrgContext, document_id: int) -> list[CostChange]:
    """The cost changes a received delivery made, each with how many recipes use the item."""
    entries = db.scalars(
        select_scoped(org, ItemCostHistory).where(
            ItemCostHistory.source_type == "receipt",
            ItemCostHistory.source_id == str(document_id),
        )
    )
    changes = []
    for entry in entries:
        item = get_scoped(db, org, InventoryItem, entry.inventory_item_id)
        if item is None:
            continue
        older = [
            e
            for e in history(db, org, entry.location_id, entry.inventory_item_id)
            if (e.effective_at, e.id) < (entry.effective_at, entry.id)
        ]
        before = Decimal(older[0].cost_per_base_cents) if older else None
        changes.append(
            CostChange(
                item,
                entry.location_id,
                before,
                Decimal(entry.cost_per_base_cents),
                len(recipes_using(db, org, item.id)),
            )
        )
    return changes


# --- what is sold without a recipe (RCP-FR-12, AC-RCP-08) ----------------------------------------


@dataclass(frozen=True)
class Unrecipied:
    name: str
    variation_id: int | None
    units: Decimal


def sold_without_recipe(
    db: Session, org: OrgContext, now: datetime, period: DateRange | None = None
) -> list[Unrecipied]:
    """Sizes sold over the period (30 days by default, in each location's business days) whose
    product has no recipe in effect, most sold first, each linking to its recipe editor."""
    span = period or DateRange("30d")
    by_sold = {
        v.source_sold_item_id: v
        for v in db.scalars(select_scoped(org, ProductVariation))
        if v.source_sold_item_id is not None
    }
    with_recipe = {
        r.product_variation_id
        for r in db.scalars(select_scoped(org, Recipe))
        if r.product_variation_id is not None and effective_version(db, org, r) is not None
    }
    sold = list(db.scalars(select_scoped(org, SoldItem)))
    names = {s.id: s.name for s in sold}
    provider_ids = {s.id: s.provider_item_id for s in sold}
    # A POS-counted item is its own stock: each sale uses one, with no recipe needed.
    counted_by_pos = {
        (item.location_id, item.provider_variation_id)
        for item in db.scalars(
            select_scoped(org, StockItem).where(
                StockItem.track_mode == TrackMode.PROVIDER,
                StockItem.provider_variation_id.is_not(None),
            )
        )
    }
    variations = {v.id: v for v in by_sold.values()}
    for v in db.scalars(select_scoped(org, ProductVariation)):
        variations.setdefault(v.id, v)
    # Keyed by the owned size where the line names one, else by the POS sale item.
    units: dict[tuple[str, int], Decimal] = {}
    for place in db.scalars(select_scoped(org, Location).where(Location.is_active.is_(True))):
        today = business_date(now, place.timezone, place.business_day_cutoff_hour)
        first, last = span.days_at(today)
        for sale in daily_sales(db, org, first, last, place.id):
            variation = variations.get(sale.product_variation_id or 0) or by_sold.get(
                sale.sold_item_id or 0
            )
            if variation is not None and variation.id in with_recipe:
                continue
            if (
                sale.sold_item_id is not None
                and (place.id, provider_ids.get(sale.sold_item_id)) in counted_by_pos
            ):
                continue
            key = ("v", variation.id) if variation else ("s", sale.sold_item_id or 0)
            if sale.units > 0:
                units[key] = units.get(key, Decimal(0)) + sale.units
    found = []
    for (kind, ident), count in units.items():
        if kind == "v":
            size = variations[ident]
            found.append(Unrecipied(f"{size.product.name} {size.name}", size.id, count))
        else:
            found.append(Unrecipied(names.get(ident, "A POS item"), None, count))
    return sorted(found, key=lambda u: (-u.units, u.name))
