"""Read models: turn ORM rows into the flat inputs the pure layer expects."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.catalog import active_stock, by_item_name
from app.models import (
    Location,
    RecipeComponent,
    SalesDaily,
    SoldItem,
    StockBatch,
    StockItem,
    TrackMode,
)
from app.transform.business_days import business_date
from app.transform.expiry import (
    Batch,
    Coverage,
    ItemExpiry,
    allocate_fefo,
    coverage,
    project_batches,
)
from app.transform.inventory import (
    ZERO,
    Component,
    SaleRow,
    StockView,
    consumption_by_day,
    daily_velocity,
)

VELOCITY_WINDOW_DAYS = 28


def stock_views(db: Session, org: OrgContext, location_id: int | None = None) -> list[StockView]:
    location_names = {loc.id: loc.name for loc in db.scalars(select_scoped(org, Location))}
    query = active_stock(org)
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
            par_level_base=Decimal(item.par_level_base)
            if item.par_level_base is not None
            else None,
            category=item.category,
            supplier=item.supplier,
            location_id=item.location_id,
            location_name=location_names.get(item.location_id, ""),
            last_counted_at=item.last_counted_at,
            tracks_expiry=item.tracks_expiry,
        )
        for item in db.scalars(by_item_name(query))
    ]


def tenant_currency(db: Session, org: OrgContext) -> str:
    """The currency money is shown in; a tenant's locations share one."""
    location = db.scalars(
        select_scoped(org, Location).where(Location.is_active.is_(True)).limit(1)
    ).first()
    return location.currency if location else "USD"


def sale_rows(db: Session, org: OrgContext, since: date | None = None) -> list[SaleRow]:
    query = select_scoped(org, SalesDaily)
    if since is not None:
        query = query.where(SalesDaily.dt >= since)
    return [
        SaleRow(row.sold_item_id, row.dt, Decimal(row.units or 0), row.net_cents)
        for row in db.scalars(query)
    ]


def sold_item_names(db: Session, org: OrgContext) -> dict[int, str]:
    return {item.id: item.name for item in db.scalars(select_scoped(org, SoldItem))}


def components(db: Session, org: OrgContext) -> list[Component]:
    return [
        Component(row.sold_item_id, row.stock_item_id, Decimal(row.qty_base_per_sale))
        for row in db.scalars(select_scoped(org, RecipeComponent))
    ]


def _provider_items(db: Session, org: OrgContext) -> list[StockItem]:
    return list(
        db.scalars(
            select_scoped(org, StockItem).where(
                StockItem.track_mode == TrackMode.PROVIDER,
                StockItem.provider_variation_id.is_not(None),
            )
        )
    )


def provider_consumption(
    db: Session, org: OrgContext, since: date | None = None
) -> dict[int, dict[date, Decimal]]:
    """Sales of POS-tracked SKUs, which have no recipe: each unit sold at a location is one gone there."""
    stock = {
        (item.location_id, item.provider_variation_id): item.id for item in _provider_items(db, org)
    }
    if not stock:
        return {}
    provider_ids = {
        sold.id: sold.provider_item_id for sold in db.scalars(select_scoped(org, SoldItem))
    }
    query = select_scoped(org, SalesDaily)
    if since is not None:
        query = query.where(SalesDaily.dt >= since)

    result: dict[int, dict[date, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for row in db.scalars(query):
        item_id = stock.get((row.location_id, provider_ids.get(row.sold_item_id)))
        if item_id is not None:
            result[item_id][row.dt] += Decimal(row.units or 0)
    return {item_id: dict(days) for item_id, days in result.items()}


def consumption(
    db: Session, org: OrgContext, since: date | None = None
) -> dict[int, dict[date, Decimal]]:
    """Base units consumed per stock item per day: via recipes, plus direct sales of POS SKUs."""
    daily = consumption_by_day(sale_rows(db, org, since), components(db, org))
    for item_id, days in provider_consumption(db, org, since).items():
        merged = daily.setdefault(item_id, {})
        for day, qty in days.items():
            merged[day] = merged.get(day, ZERO) + qty
    return daily


def sales_signal(db: Session, org: OrgContext) -> set[int]:
    """Stock items whose depletion shows up in sales: POS-tracked, or linked by a recipe.

    For anything else a velocity of zero means "unknown", not "not selling".
    """
    linked = {row.stock_item_id for row in db.scalars(select_scoped(org, RecipeComponent))}
    return linked | {item.id for item in _provider_items(db, org)}


def batches_by_item(db: Session, org: OrgContext) -> dict[int, list[Batch]]:
    result: dict[int, list[Batch]] = defaultdict(list)
    for row in db.scalars(select_scoped(org, StockBatch)):
        result[row.stock_item_id].append(
            Batch(
                id=row.id,
                received_qty_base=Decimal(row.received_qty_base),
                expires_on=row.expires_on,
                cost_per_base_cents=(
                    Decimal(row.cost_per_base_cents)
                    if row.cost_per_base_cents is not None
                    else None
                ),
                received_at=row.received_at,
            )
        )
    return dict(result)


def business_dates(db: Session, org: OrgContext, now: datetime) -> dict[int, date]:
    """Each location's business day at this instant, in its own time zone and cutoff."""
    return {
        location.id: business_date(now, location.timezone, location.business_day_cutoff_hour)
        for location in db.scalars(select_scoped(org, Location))
    }


def expiry_positions(
    db: Session,
    org: OrgContext,
    today: date | None = None,
    location_id: int | None = None,
    velocity: dict[int, Decimal] | None = None,
    now: datetime | None = None,
) -> list[ItemExpiry]:
    """Every item that holds dated stock, with each lot positioned and projected for its day.

    A given today is used for every item; otherwise each item's day is its location's business
    day at now (AC-ADM-01), so a lot dated today in Los Angeles is not expired by UTC's midnight.
    """
    now = now or datetime.now(UTC)
    batches = batches_by_item(db, org)
    if not batches:
        return []
    days = {} if today is not None else business_dates(db, org, now)
    velocity = velocity if velocity is not None else velocity_map(db, org, today or now.date())
    signal = sales_signal(db, org)

    result = []
    for item in stock_views(db, org, location_id):
        lots = batches.get(item.id)
        if not lots:
            continue
        speed = velocity.get(item.id, ZERO) if item.id in signal else None
        day = today or days.get(item.location_id) or now.date()
        positions, undated = project_batches(
            item.qty_base, lots, speed, day, item.cost_per_base_cents
        )
        result.append(ItemExpiry(item, positions, undated))
    return result


def velocity_map(
    db: Session,
    org: OrgContext,
    today: date | None = None,
    window_days: int = VELOCITY_WINDOW_DAYS,
) -> dict[int, Decimal]:
    """Base units per day per stock item over the trailing window."""
    today = today or datetime.now(UTC).date()
    window_start = today - timedelta(days=window_days - 1)
    daily = consumption(db, org, window_start)
    return {item_id: daily_velocity(days, window_days, today) for item_id, days in daily.items()}


def expiry_coverage(db: Session, org: OrgContext, location_id: int | None = None) -> Coverage:
    """Coverage across stock that can expire: everything not dismissed as never expiring."""
    batches = batches_by_item(db, org)
    entries = []
    for item in stock_views(db, org, location_id):
        if item.tracks_expiry is False:
            continue
        _, undated = allocate_fefo(item.qty_base, batches.get(item.id, []))
        entries.append((item, undated))
    return coverage(entries)
