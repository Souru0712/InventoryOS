"""Reorder suggestions (T2.15; ROR-FR-01..05, ROR-FR-07, ROR-FR-08, STD-FR-09, WF-21).

Reads only: stock rows, the ledger, count sessions, sales coverage and suppliers. The rules are
in `app/transform/reorder.py`. Suggestions are a list for a person to act on; nothing here
places an order (ROR-FR-08, AC-ROR-05).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.ledger.history import balances_before, item_moves
from app.models import (
    Location,
    StockCount,
    StockCountLine,
    StockItem,
    Supplier,
    SupplierItem,
)
from app.orchestration.views import stock_views
from app.sales import daily_sales, source_of_record
from app.transform.business_days import business_date, business_day_end, business_day_start
from app.transform.reorder import (
    DEFAULT_COVER_DAYS,
    DEFAULT_SAFETY_PERCENT,
    DEFAULT_WINDOW,
    Group,
    Inputs,
    Line,
    Move,
    Observation,
    check_settings,
    count_based_use,
    count_first,
    daily_use,
    group,
    needs_order,
    observations,
    order_by,
    recorded_use,
    suggest,
)
from app.units import Dimension

# Sources whose days are complete only when someone recorded that day's sales.
HAND_RECORDED = frozenset({"manual", "file_import"})


def _complete(db: Session, org: OrgContext, place: Location, first: date, last: date) -> bool:
    """Every open day from first to last has its sales: from a POS, or recorded for the day."""
    recorded = {sale.day for sale in daily_sales(db, org, first, last, place.id)}
    day = first
    while day <= last:
        if place.open_days >> day.weekday() & 1:
            source = source_of_record(db, org, place.id, day)
            if source is None or (source.kind in HAND_RECORDED and day not in recorded):
                return False
        day += timedelta(days=1)
    return True


def _suppliers(db: Session, org: OrgContext) -> dict[int, tuple[Supplier, SupplierItem]]:
    """Each item's supplier to order from: its preferred active supplier item, else its only one."""
    found: dict[int, list[SupplierItem]] = defaultdict(list)
    for row in db.scalars(select_scoped(org, SupplierItem).where(SupplierItem.is_active.is_(True))):
        if row.supplier.is_active:
            found[row.inventory_item_id].append(row)
    chosen = {}
    for item_id, rows in found.items():
        preferred = [r for r in rows if r.preferred]
        pick = preferred[0] if preferred else rows[0] if len(rows) == 1 else None
        if pick is not None:
            chosen[item_id] = (pick.supplier, pick)
    return chosen


def plan(
    db: Session,
    org: OrgContext,
    *,
    location_id: int | None = None,
    window: int = DEFAULT_WINDOW,
    cover_days: int = DEFAULT_COVER_DAYS,
    safety_percent: Decimal = DEFAULT_SAFETY_PERCENT,
    now: datetime | None = None,
) -> list[Group]:
    """What to order, grouped by supplier, each line with the inputs behind its number."""
    check_settings(window, cover_days, safety_percent)
    now = now or datetime.now(UTC)
    places = {p.id: p for p in db.scalars(select_scoped(org, Location))}
    today = {
        p.id: business_date(now, p.timezone, p.business_day_cutoff_hour) for p in places.values()
    }
    windows = {
        p.id: (
            business_day_start(
                today[p.id] - timedelta(days=window - 1), p.timezone, p.business_day_cutoff_hour
            ),
            business_day_end(today[p.id], p.timezone, p.business_day_cutoff_hour),
        )
        for p in places.values()
        if location_id is None or p.id == location_id
    }
    moves: dict[tuple[int, int], list[Move]] = defaultdict(list)
    for m in item_moves(db, org, windows):
        place = places[m.location_id]
        moves[(m.location_id, m.inventory_item_id)].append(
            Move(
                m.occurred_at,
                business_date(m.occurred_at, place.timezone, place.business_day_cutoff_hour),
                m.transaction_id,
                m.kind,
                m.source_type,
                m.movement_type,
                m.qty_base,
            )
        )
    opening = balances_before(db, org, windows)
    counted: dict[tuple[int, int], list[Observation]] = defaultdict(list)
    for line, session in db.execute(
        select_scoped(org, StockCountLine)
        .add_columns(StockCount)
        .join(StockCount, StockCount.id == StockCountLine.stock_count_id)
        .where(StockCount.status == "posted", StockCount.location_id.in_(list(windows)))
    ).tuples():
        start, end = windows[session.location_id]
        if start <= line.counted_at <= end:
            place = places[session.location_id]
            counted[(session.location_id, line.inventory_item_id)].append(
                Observation(
                    line.counted_at,
                    business_date(line.counted_at, place.timezone, place.business_day_cutoff_hour),
                    Decimal(line.counted_qty_base),
                )
            )
    complete = {
        pid: _complete(
            db, org, places[pid], today[pid] - timedelta(days=window - 1), today[pid] - timedelta(1)
        )
        for pid in windows
    }
    catalog = {row.id: row.inventory_item_id for row in db.scalars(select_scoped(org, StockItem))}
    suppliers = _suppliers(db, org)

    lines = []
    for view in stock_views(db, org, location_id):
        item_id = catalog.get(view.id)
        if item_id is None or view.location_id not in windows:
            continue
        key = (view.location_id, item_id)
        found = moves.get(key, [])
        counts = observations(opening.get(key, Decimal(0)), found, counted.get(key, []))
        usage = daily_use(
            recorded_use(found), window, complete[view.location_id], count_based_use(found, counts)
        )
        supplier, offer = suppliers.get(item_id, (None, None))
        inputs = Inputs(
            per_day=usage.per_day,
            lead_days=supplier.lead_time_days if supplier is not None else view.lead_time_days,
            cover_days=cover_days,
            safety_percent=safety_percent,
            reorder_point=Decimal(view.reorder_point_base),
            on_hand=Decimal(view.qty_base),
            pack=Decimal(offer.pack_size_base) if offer is not None else None,
            whole_units=view.dimension is Dimension.COUNT,
        )
        if not needs_order(inputs):
            continue
        first = count_first(inputs.on_hand, view.last_counted_at, now)
        lines.append(
            Line(
                stock_item_id=view.id,
                name=view.name,
                location_name=view.location_name,
                unit=view.stock_unit,
                supplier=supplier.name if supplier is not None else (view.supplier or ""),
                purchase_unit=offer.purchase_unit if offer is not None else None,
                usage=usage,
                inputs=inputs,
                order_by=order_by(
                    today[view.location_id],
                    inputs,
                    supplier.delivery_days if supplier is not None else 0,
                ),
                count_first=first,
                suggestion=None if first else suggest(inputs),
            )
        )
    return group(lines)
