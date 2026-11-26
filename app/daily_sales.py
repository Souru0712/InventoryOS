"""Daily sales entered by hand (T2.12; STD-FR-02, -05, -06, PD-37, PD-38, AC-SAL-02, -03, -05,
ADR-0063).

A business with no POS records each business day's sales: how many of each product size (PD-38),
as one manual daily transaction per location and day (reference `day:<date>`). Saving the same
values again records nothing (AC-SAL-02); a changed day is the next revision, and the reason the
person gives travels to the reversal of what the day had taken (STD-FR-05, AC-SAL-03). The day's
consumption posts straight away, the queued job being the safety net. A day whose sales come
from another source (a POS) is not entered by hand (AC-SAL-06). An open day with no sales by the
next day raises `sales_missing` until it is entered (STD-FR-06, AC-SAL-05). Saving a day with
nothing sold confirms it as a day without sales (PD-54), and a day marked closed takes no
entries until it is reopened.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.consumption import consume_sale
from app.location_days import CLOSED, clear_no_sales, confirm_no_sales, mark_of, marks
from app.models import (
    Location,
    ProductVariation,
    SalesSource,
    SalesTransaction,
)
from app.sales import (
    NotFound,
    add_source,
    daily_sales,
    extend_source,
    record_sale,
    source_of_record,
    sources,
)
from app.transform.business_days import business_date
from app.transform.exceptions import Finding
from app.transform.sales import ORIGIN_LABELS, SaleLine, SalesRuleError

log = logging.getLogger(__name__)
LOOK_BACK_DAYS = 7  # how far back a missing day is still flagged


def _reference(day: date) -> str:
    return f"day:{day.isoformat()}"


def manual_source(db: Session, org: OrgContext, location_id: int, day: date) -> SalesSource:
    """The location's hand-entry source for the day; refused when another source is of record
    that day (DR-31). A first entry starts one; an earlier day extends it back."""
    current = source_of_record(db, org, location_id, day)
    if current is not None:
        if current.kind != "manual":
            raise SalesRuleError(
                f"Sales for {day:%d %b %Y} come from {ORIGIN_LABELS[current.kind].lower()}, "
                "so they are not entered by hand."
            )
        return current
    later = [
        s
        for s in sources(db, org, location_id)
        if s.kind == "manual" and s.of_record and s.active_from > day
    ]
    if later:
        return extend_source(db, org, min(later, key=lambda s: s.active_from).id, day)
    return add_source(db, org, location_id=location_id, kind="manual", active_from=day)


def entered(db: Session, org: OrgContext, location_id: int, day: date) -> dict[int, Decimal]:
    """What the day holds now, per product size."""
    found: dict[int, Decimal] = {}
    for sale in daily_sales(db, org, day, day, location_id):
        if sale.product_variation_id is not None:
            found[sale.product_variation_id] = (
                found.get(sale.product_variation_id, Decimal(0)) + sale.units
            )
    return found


def save_day(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    day: date,
    quantities: dict[int, Decimal],
    reason: str = "",
) -> SalesTransaction | None:
    """Record a day's sales; None when nothing changed. Changing a saved day needs a reason. A
    first save with nothing sold confirms the day as open without sales (PD-54)."""
    place = get_scoped(db, org, Location, location_id)
    if place is None:
        raise NotFound(location_id)
    mark = mark_of(db, org, place.id, day)
    if mark is not None and mark.status == CLOSED:
        raise SalesRuleError(
            f"{day:%d %b %Y} is marked closed ({mark.reason}). Reopen the day to enter its sales."
        )
    if any(qty < 0 for qty in quantities.values()):
        raise SalesRuleError("A quantity sold cannot be below zero.")
    for variation_id in quantities:
        if get_scoped(db, org, ProductVariation, variation_id) is None:
            raise NotFound(variation_id)
    wanted = {v: q for v, q in quantities.items() if q > 0}
    before = entered(db, org, place.id, day)
    if not wanted and not before:
        # Nothing sold is a confirmed day, not an empty one (PD-54).
        manual_source(db, org, place.id, day)
        confirm_no_sales(db, org, place.id, day)
        return None
    if wanted == before:
        return None
    if before and not reason.strip():
        raise SalesRuleError("Say why the day's sales are corrected.")
    source = manual_source(db, org, place.id, day)
    sale = record_sale(
        db,
        org,
        source,
        source_reference=_reference(day),
        lines=[SaleLine(f"v:{vid}", qty, vid) for vid, qty in sorted(wanted.items())],
        day=day,
        reason=reason.strip() or None,
    )
    if sale is None:
        return None
    if wanted:
        clear_no_sales(db, org, place.id, day)
    else:
        confirm_no_sales(db, org, place.id, day)  # corrected to nothing sold
    try:
        with db.begin_nested():
            consume_sale(db, org, sale.id, reason.strip() or None)
    except Exception:  # noqa: BLE001 - the queued job retries it; the sale is recorded
        log.exception("consumption of sale %s left to the job queue", sale.id)
    return sale


def missing_days(db: Session, org: OrgContext, now: datetime) -> list[Finding]:
    """Open days at hand-entry locations with no sales recorded by the next day (AC-SAL-05), unless
    a person confirmed the day without sales or closed it (PD-54)."""
    found = []
    for place in db.scalars(select_scoped(org, Location).where(Location.is_active.is_(True))):
        today = business_date(now, place.timezone, place.business_day_cutoff_hour)
        marked = marks(db, org, place.id, today - timedelta(days=LOOK_BACK_DAYS), today)
        for back in range(1, LOOK_BACK_DAYS + 1):
            day = today - timedelta(days=back)
            if not place.open_days >> day.weekday() & 1 or day in marked:
                continue
            source = source_of_record(db, org, place.id, day)
            if source is None or source.kind != "manual":
                continue
            has_sales = db.scalars(
                select_scoped(org, SalesTransaction).where(
                    SalesTransaction.location_id == place.id,
                    SalesTransaction.business_date == day,
                    SalesTransaction.status == "completed",
                    SalesTransaction.of_record.is_(True),
                )
            ).first()
            if has_sales is None:
                found.append(
                    Finding(
                        type="sales_missing",
                        subject_type="location_day",
                        subject_id=f"{place.id}:{day.isoformat()}",
                        title=f"{place.name} has no sales entered for {day:%a %d %b}.",
                        location_id=place.id,
                    )
                )
    return found
