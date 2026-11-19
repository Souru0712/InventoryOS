"""Operational reports (T2.16; ANL-FR-03, -05, -07, AC-ANL-03, -05).

Reads only: ledger movements over each location's own business days (its time zone and cutoff
hour, AC-ANL-05), waste events for their reason, and batches for stock past its date. The
figures are worked out in `app/transform/analytics.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.ledger.history import ItemMove, item_moves
from app.ledger.posting import batch_remaining
from app.models import Location, StockBatch, StockItem, WasteEvent, WasteReason
from app.transform.analytics import (
    AtRisk,
    ConsumptionReport,
    ExpiryReport,
    Moved,
    VarianceReport,
    consumption,
    count_variance,
    expiry_losses,
)
from app.transform.business_days import business_date, business_day_end, business_day_start
from app.transform.dashboard import DateRange
from app.transform.inventory import valuation_cents

EXPIRED_REASON = "expiration"  # the waste reason code for stock past its date (WST-FR-02)
WASTE_SOURCE = "waste_event"


def _places(db: Session, org: OrgContext, location_id: int | None) -> list[Location]:
    query = select_scoped(org, Location)
    if location_id is not None:
        query = query.where(Location.id == location_id)
    return list(db.scalars(query.order_by(Location.id)))


def days(
    db: Session, org: OrgContext, period: DateRange, now: datetime, location_id: int | None
) -> dict[int, tuple[date, date]]:
    """The period's first and last business day at each location, against its own today."""
    return {
        p.id: period.days_at(business_date(now, p.timezone, p.business_day_cutoff_hour))
        for p in _places(db, org, location_id)
    }


def _windows(
    db: Session, org: OrgContext, period: DateRange, now: datetime, location_id: int | None
) -> dict[int, tuple[datetime, datetime]]:
    spans = days(db, org, period, now, location_id)
    return {
        p.id: (
            business_day_start(spans[p.id][0], p.timezone, p.business_day_cutoff_hour),
            business_day_end(spans[p.id][1], p.timezone, p.business_day_cutoff_hour),
        )
        for p in _places(db, org, location_id)
    }


def _expired_events(db: Session, org: OrgContext, moves: list[ItemMove]) -> set[str]:
    ids = {int(m.source_id) for m in moves if m.source_type == WASTE_SOURCE and m.source_id}
    if not ids:
        return set()
    expired = {
        r.id
        for r in db.scalars(
            select_scoped(org, WasteReason).where(WasteReason.code == EXPIRED_REASON)
        )
    }
    return {
        str(e.id)
        for e in db.scalars(select_scoped(org, WasteEvent).where(WasteEvent.id.in_(ids)))
        if e.reason_id in expired
    }


def _moved(
    db: Session, org: OrgContext, period: DateRange, now: datetime, location_id: int | None
) -> list[Moved]:
    moves = item_moves(db, org, _windows(db, org, period, now, location_id))
    expired = _expired_events(db, org, moves)
    return [
        Moved(
            m.location_id,
            m.inventory_item_id,
            m.movement_type,
            m.qty_base,
            m.unit_cost,
            m.source_type == WASTE_SOURCE and m.source_id in expired,
        )
        for m in moves
    ]


def consumption_report(
    db: Session,
    org: OrgContext,
    period: DateRange,
    *,
    location_id: int | None = None,
    now: datetime | None = None,
) -> ConsumptionReport:
    """Ingredient use by item and period: sales, production, waste, manual (ANL-FR-03)."""
    return consumption(_moved(db, org, period, now or datetime.now(UTC), location_id))


def variance_report(
    db: Session,
    org: OrgContext,
    period: DateRange,
    *,
    location_id: int | None = None,
    now: datetime | None = None,
) -> VarianceReport:
    """Count variances by item and period, quantity and value (ANL-FR-07)."""
    return count_variance(_moved(db, org, period, now or datetime.now(UTC), location_id))


def expiry_report(
    db: Session,
    org: OrgContext,
    period: DateRange,
    *,
    location_id: int | None = None,
    now: datetime | None = None,
) -> ExpiryReport:
    """Waste of stock past its date over the period, and expired stock still held (ANL-FR-05):
    a batch dated before its location's today that holds stock, at its own cost, else the
    item's current cost."""
    now = now or datetime.now(UTC)
    places = {p.id: p for p in _places(db, org, location_id)}
    today = {
        pid: business_date(now, p.timezone, p.business_day_cutoff_hour) for pid, p in places.items()
    }
    stock = {
        s.id: s
        for s in db.scalars(select_scoped(org, StockItem))
        if s.location_id in places and s.inventory_item_id is not None
    }
    batches = [
        b
        for b in db.scalars(select_scoped(org, StockBatch))
        if b.stock_item_id in stock and b.expires_on < today[stock[b.stock_item_id].location_id]
    ]
    held = batch_remaining(db, org, [b.id for b in batches])
    risky = []
    for batch in batches:
        remaining = held.get(batch.id)
        if remaining is None or remaining <= 0:
            continue
        row = stock[batch.stock_item_id]
        cost = batch.cost_per_base_cents or row.cost_per_base_cents
        risky.append(
            AtRisk(
                row.location_id,
                row.inventory_item_id or 0,
                batch.id,
                batch.expires_on,
                remaining,
                valuation_cents(remaining, cost) if cost else None,
            )
        )
    return expiry_losses(_moved(db, org, period, now, location_id), risky)
