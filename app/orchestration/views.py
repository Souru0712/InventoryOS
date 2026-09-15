"""Read models: turn ORM rows into the flat inputs the pure layer expects."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    InventoryCount,
    Location,
    RecipeComponent,
    SalesDaily,
    SoldItem,
    StockBatch,
    StockItem,
    TrackMode,
)
from app.transform.expiry import Batch, Coverage, ItemExpiry, allocate_fefo, coverage, project_batches
from app.transform.inventory import (
    ZERO,
    Component,
    SaleRow,
    StockView,
    consumption_by_day,
    daily_velocity,
    derived_quantity,
)

VELOCITY_WINDOW_DAYS = 28


def stock_views(db: Session, tenant_id: int, location_id: int | None = None) -> list[StockView]:
    location_names = {
        loc.id: loc.name
        for loc in db.scalars(select(Location).where(Location.tenant_id == tenant_id))
    }
    query = select(StockItem).where(
        StockItem.tenant_id == tenant_id, StockItem.is_active.is_(True)
    )
    if location_id is not None:
        query = query.where(StockItem.location_id == location_id)

    return [
        StockView(
            id=item.id,
            slug=item.slug,
            name=item.name,
            dimension=item.dimension,
            stock_unit=item.stock_unit,
            track_mode=item.track_mode.value,
            qty_base=Decimal(item.qty_base or 0),
            reorder_point_base=Decimal(item.reorder_point_base or 0),
            cost_per_base_cents=Decimal(item.cost_per_base_cents or 0),
            retail_per_base_cents=(
                Decimal(item.retail_per_base_cents)
                if item.retail_per_base_cents is not None
                else None
            ),
            lead_time_days=item.lead_time_days,
            par_level_base=Decimal(item.par_level_base) if item.par_level_base is not None else None,
            category=item.category,
            supplier=item.supplier,
            location_id=item.location_id,
            location_name=location_names.get(item.location_id, ""),
            last_counted_at=item.last_counted_at,
            tracks_expiry=item.tracks_expiry,
        )
        for item in db.scalars(query.order_by(StockItem.name))
    ]


def tenant_currency(db: Session, tenant_id: int) -> str:
    """The currency money is shown in; a tenant's locations share one."""
    location = db.scalars(
        select(Location).where(Location.tenant_id == tenant_id, Location.is_active.is_(True)).limit(1)
    ).first()
    return location.currency if location else "USD"


def sale_rows(db: Session, tenant_id: int, since: date | None = None) -> list[SaleRow]:
    query = select(SalesDaily).where(SalesDaily.tenant_id == tenant_id)
    if since is not None:
        query = query.where(SalesDaily.dt >= since)
    return [
        SaleRow(row.sold_item_id, row.dt, Decimal(row.units or 0), row.net_cents)
        for row in db.scalars(query)
    ]


def sold_item_names(db: Session, tenant_id: int) -> dict[int, str]:
    return {
        item.id: item.name
        for item in db.scalars(select(SoldItem).where(SoldItem.tenant_id == tenant_id))
    }


def components(db: Session, tenant_id: int) -> list[Component]:
    return [
        Component(row.sold_item_id, row.stock_item_id, Decimal(row.qty_base_per_sale))
        for row in db.scalars(
            select(RecipeComponent).where(RecipeComponent.tenant_id == tenant_id)
        )
    ]


def _provider_items(db: Session, tenant_id: int) -> list[StockItem]:
    return list(
        db.scalars(
            select(StockItem).where(
                StockItem.tenant_id == tenant_id,
                StockItem.track_mode == TrackMode.PROVIDER,
                StockItem.provider_variation_id.is_not(None),
            )
        )
    )


def provider_consumption(
    db: Session, tenant_id: int, since: date | None = None
) -> dict[int, dict[date, Decimal]]:
    """Sales of POS-tracked SKUs, which have no recipe: each unit sold at a location is one gone there."""
    stock = {
        (item.location_id, item.provider_variation_id): item.id
        for item in _provider_items(db, tenant_id)
    }
    if not stock:
        return {}
    provider_ids = {
        sold.id: sold.provider_item_id
        for sold in db.scalars(select(SoldItem).where(SoldItem.tenant_id == tenant_id))
    }
    query = select(SalesDaily).where(SalesDaily.tenant_id == tenant_id)
    if since is not None:
        query = query.where(SalesDaily.dt >= since)

    result: dict[int, dict[date, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for row in db.scalars(query):
        item_id = stock.get((row.location_id, provider_ids.get(row.sold_item_id)))
        if item_id is not None:
            result[item_id][row.dt] += Decimal(row.units or 0)
    return {item_id: dict(days) for item_id, days in result.items()}


def consumption(db: Session, tenant_id: int, since: date | None = None) -> dict[int, dict[date, Decimal]]:
    """Base units consumed per stock item per day: via recipes, plus direct sales of POS SKUs."""
    daily = consumption_by_day(sale_rows(db, tenant_id, since), components(db, tenant_id))
    for item_id, days in provider_consumption(db, tenant_id, since).items():
        merged = daily.setdefault(item_id, {})
        for day, qty in days.items():
            merged[day] = merged.get(day, ZERO) + qty
    return daily


def sales_signal(db: Session, tenant_id: int) -> set[int]:
    """Stock items whose depletion shows up in sales: POS-tracked, or linked by a recipe.

    For anything else a velocity of zero means "unknown", not "not selling".
    """
    linked = {
        row.stock_item_id
        for row in db.scalars(select(RecipeComponent).where(RecipeComponent.tenant_id == tenant_id))
    }
    return linked | {item.id for item in _provider_items(db, tenant_id)}


def batches_by_item(db: Session, tenant_id: int) -> dict[int, list[Batch]]:
    result: dict[int, list[Batch]] = defaultdict(list)
    for row in db.scalars(select(StockBatch).where(StockBatch.tenant_id == tenant_id)):
        result[row.stock_item_id].append(
            Batch(
                id=row.id,
                received_qty_base=Decimal(row.received_qty_base),
                expires_on=row.expires_on,
                cost_per_base_cents=(
                    Decimal(row.cost_per_base_cents) if row.cost_per_base_cents is not None else None
                ),
                received_at=row.received_at,
            )
        )
    return dict(result)


def expiry_positions(
    db: Session,
    tenant_id: int,
    today: date | None = None,
    location_id: int | None = None,
    velocity: dict[int, Decimal] | None = None,
) -> list[ItemExpiry]:
    """Every item that holds dated stock, with each lot positioned and projected for today."""
    today = today or datetime.now(timezone.utc).date()
    batches = batches_by_item(db, tenant_id)
    if not batches:
        return []
    velocity = velocity if velocity is not None else velocity_map(db, tenant_id, today)
    signal = sales_signal(db, tenant_id)

    result = []
    for item in stock_views(db, tenant_id, location_id):
        lots = batches.get(item.id)
        if not lots:
            continue
        speed = velocity.get(item.id, ZERO) if item.id in signal else None
        positions, undated = project_batches(
            item.qty_base, lots, speed, today, item.cost_per_base_cents
        )
        result.append(ItemExpiry(item, positions, undated))
    return result


def velocity_map(
    db: Session,
    tenant_id: int,
    today: date | None = None,
    window_days: int = VELOCITY_WINDOW_DAYS,
) -> dict[int, Decimal]:
    """Base units per day per stock item over the trailing window."""
    today = today or datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=window_days - 1)
    daily = consumption(db, tenant_id, window_start)
    return {item_id: daily_velocity(days, window_days, today) for item_id, days in daily.items()}


def refresh_derived_quantities(db: Session, tenant_id: int, today: date | None = None) -> int:
    """Recompute weight/volume quantities: last physical count minus consumption since."""
    today = today or datetime.now(timezone.utc).date()
    items = list(
        db.scalars(
            select(StockItem).where(
                StockItem.tenant_id == tenant_id,
                StockItem.track_mode == TrackMode.DERIVED,
                StockItem.is_active.is_(True),
            )
        )
    )
    if not items:
        return 0

    daily = consumption(db, tenant_id)
    updated = 0
    for item in items:
        anchor = db.scalars(
            select(InventoryCount)
            .where(InventoryCount.stock_item_id == item.id)
            .order_by(InventoryCount.counted_at.desc())
            .limit(1)
        ).first()
        if anchor is None:
            continue
        quantity = derived_quantity(
            Decimal(anchor.qty_base), _aware(anchor.counted_at), daily.get(item.id, {}), today
        )
        if item.qty_base != quantity:
            item.qty_base = quantity
            updated += 1
        item.last_counted_at = anchor.counted_at
    db.flush()
    return updated


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

def expiry_coverage(
    db: Session, tenant_id: int, location_id: int | None = None
) -> Coverage:
    """Coverage across stock that can expire: everything not dismissed as never expiring."""
    batches = batches_by_item(db, tenant_id)
    entries = []
    for item in stock_views(db, tenant_id, location_id):
        if item.tracks_expiry is False:
            continue
        _, undated = allocate_fefo(item.qty_base, batches.get(item.id, []))
        entries.append((item, undated))
    return coverage(entries)
