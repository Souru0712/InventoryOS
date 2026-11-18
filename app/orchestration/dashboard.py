"""The dashboard's figures (T1.25; CMD-FR-01..04, -10, -13, -14, ADR-0049).

Every figure comes from the ledger or the exceptions: money over a period from movements at the
cost they were posted with, in each location's own business days; what needs attention from
open exceptions. Nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.exceptions import ExceptionView, listing
from app.ledger.history import MovementTotal, movement_totals
from app.models import Location, RecipeComponent, SoldItem, StockItem, TrackMode
from app.sales import daily_sales
from app.transform.business_days import business_date, business_day_end, business_day_start
from app.transform.dashboard import DateRange, completeness_note

ATTENTION_SHOWN = 8


@dataclass(frozen=True)
class PeriodMoney:
    """Money that moved over the chosen period, at posted cost (CMD-FR-10)."""

    waste: MovementTotal
    received: MovementTotal
    count_variance: MovementTotal


def places(db: Session, org: OrgContext, location_id: int | None = None) -> list[Location]:
    query = select_scoped(org, Location).where(Location.is_active.is_(True))
    if location_id is not None:
        query = query.where(Location.id == location_id)
    return list(db.scalars(query.order_by(Location.name)))


def _days(location: Location, period: DateRange, now: datetime) -> tuple[date, date]:
    today = business_date(now, location.timezone, location.business_day_cutoff_hour)
    return period.days_at(today)


def period_money(
    db: Session, org: OrgContext, period: DateRange, now: datetime, location_id: int | None = None
) -> PeriodMoney:
    """Waste, receipts and count variances over the period, each location in its own days."""
    windows = {}
    for location in places(db, org, location_id):
        first, last = _days(location, period, now)
        cutoff = location.business_day_cutoff_hour
        windows[location.id] = (
            business_day_start(first, location.timezone, cutoff),
            business_day_end(last, location.timezone, cutoff),
        )
    totals = movement_totals(db, org, windows, ("waste", "receipt", "count_variance"))
    return PeriodMoney(totals["waste"], totals["receipt"], totals["count_variance"])


def sales_without_recipe(
    db: Session, org: OrgContext, period: DateRange, now: datetime, location_id: int | None = None
) -> str | None:
    """The completeness notice (CMD-FR-13): units sold over the period of products that use no
    stock, because they have no recipe and are not a POS-counted item themselves."""
    with_recipe = {row.sold_item_id for row in db.scalars(select_scoped(org, RecipeComponent))}
    counted_by_pos = {
        (item.location_id, item.provider_variation_id)
        for item in db.scalars(
            select_scoped(org, StockItem).where(
                StockItem.track_mode == TrackMode.PROVIDER,
                StockItem.provider_variation_id.is_not(None),
            )
        )
    }
    provider_ids = {
        sold.id: sold.provider_item_id for sold in db.scalars(select_scoped(org, SoldItem))
    }
    units = Decimal("0")
    products: set[int] = set()
    for location in places(db, org, location_id):
        first, last = _days(location, period, now)
        for sale in daily_sales(db, org, first, last, location.id):
            if sale.sold_item_id is None or sale.sold_item_id in with_recipe:
                continue
            if (location.id, provider_ids.get(sale.sold_item_id)) in counted_by_pos:
                continue
            if sale.units > 0:
                units += sale.units
                products.add(sale.sold_item_id)
    return completeness_note(units, len(products))


def needs_attention(
    db: Session, org: OrgContext, location_id: int | None = None
) -> list[ExceptionView]:
    """Open exceptions in needs-attention order (CMD-FR-01); a location's view keeps the
    business-wide ones. Financial ones and every amount are already gone for a role without
    cost access (REC-FR-16)."""
    return [
        view
        for view in listing(db, org)
        if location_id is None or view.location_id in (None, location_id)
    ]
