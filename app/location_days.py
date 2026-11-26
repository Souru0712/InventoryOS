"""Days a location accounts for without sales (PD-54, C-54).

A Standalone day is expected to have its sales recorded. A person may instead confirm that the
location was open and sold nothing (saving an all-zero day does this), or mark a scheduled day
closed that once, with a reason. Both count for coverage: a confirmed day is covered, a closed
day is not expected. The only writer of `location_days`; it never changes sales or the ledger, so
a day with sales cannot be marked closed until its sales are corrected. Closing and reopening are
audited.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import exists
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.audit import record_audit
from app.models import Location, LocationDay, SalesTransaction, SalesTransactionLine
from app.sales import NotFound, source_of_record
from app.transform.analytics import STANDALONE_ORIGINS
from app.transform.sales import ORIGIN_LABELS

NO_SALES, CLOSED = "no_sales", "closed"
REASON_LIMIT = 200


class DayRuleError(ValueError):
    """A mark the rules refuse; nothing is written."""


def _place(db: Session, org: OrgContext, location_id: int) -> Location:
    place = get_scoped(db, org, Location, location_id)
    if place is None:
        raise NotFound(location_id)
    return place


def mark_of(db: Session, org: OrgContext, location_id: int, day: date) -> LocationDay | None:
    return db.scalars(
        select_scoped(org, LocationDay).where(
            LocationDay.location_id == location_id, LocationDay.business_date == day
        )
    ).first()


def marks(
    db: Session, org: OrgContext, location_id: int, first: date, last: date
) -> dict[date, str]:
    """Each marked day's status over a span."""
    return {
        row.business_date: row.status
        for row in db.scalars(
            select_scoped(org, LocationDay).where(
                LocationDay.location_id == location_id,
                LocationDay.business_date >= first,
                LocationDay.business_date <= last,
            )
        )
    }


def has_sales(db: Session, org: OrgContext, location_id: int, day: date) -> bool:
    """Whether counted sales with at least one line are recorded for the day."""
    line = exists().where(SalesTransactionLine.sales_transaction_id == SalesTransaction.id)
    return (
        db.scalars(
            select_scoped(org, SalesTransaction).where(
                SalesTransaction.location_id == location_id,
                SalesTransaction.business_date == day,
                SalesTransaction.status == "completed",
                SalesTransaction.of_record.is_(True),
                line,
            )
        ).first()
        is not None
    )


def _check_source(db: Session, org: OrgContext, place: Location, day: date) -> None:
    record = source_of_record(db, org, place.id, day)
    if record is not None and record.kind not in STANDALONE_ORIGINS:
        raise DayRuleError(
            f"Sales for {day:%d %b %Y} come from {ORIGIN_LABELS[record.kind].lower()}, "
            "so the day is not marked by hand."
        )


def _put(
    db: Session, org: OrgContext, place: Location, day: date, status: str, reason: str | None
) -> LocationDay:
    row = mark_of(db, org, place.id, day)
    if row is None:
        row = LocationDay(tenant_id=org.tenant_id, location_id=place.id, business_date=day)
        db.add(row)
    row.status, row.reason, row.actor_user_id = status, reason, org.user_id
    db.flush()
    return row


def confirm_no_sales(db: Session, org: OrgContext, location_id: int, day: date) -> LocationDay:
    """Record that the location was open on the day and sold nothing (an all-zero day)."""
    place = _place(db, org, location_id)
    _check_source(db, org, place, day)
    row = mark_of(db, org, place.id, day)
    if row is not None and row.status == CLOSED:
        raise DayRuleError(f"{day:%d %b %Y} is marked closed. Reopen it to record the day.")
    if has_sales(db, org, place.id, day):
        raise DayRuleError(f"{day:%d %b %Y} already has sales recorded.")
    if row is not None:
        return row
    return _put(db, org, place, day, NO_SALES, None)


def clear_no_sales(db: Session, org: OrgContext, location_id: int, day: date) -> None:
    """Sales were recorded for a day confirmed as having none: the sales speak for it now."""
    row = mark_of(db, org, location_id, day)
    if row is not None and row.status == NO_SALES:
        db.delete(row)
        db.flush()


def close_day(
    db: Session,
    org: OrgContext,
    location_id: int,
    day: date,
    reason: str,
    *,
    ip: str | None = None,
) -> LocationDay:
    """Mark a scheduled day closed that once (a holiday, a repair), with its reason."""
    place = _place(db, org, location_id)
    reason = reason.strip()
    if not reason:
        raise DayRuleError("Say why the location was closed.")
    if len(reason) > REASON_LIMIT:
        raise DayRuleError(f"Keep the reason under {REASON_LIMIT} characters.")
    _check_source(db, org, place, day)
    if has_sales(db, org, place.id, day):
        raise DayRuleError(
            f"{day:%d %b %Y} has sales recorded, so it was not closed. Correct the day's sales "
            "first if they are wrong."
        )
    before = mark_of(db, org, place.id, day)
    previous = {"status": before.status, "reason": before.reason} if before else None
    row = _put(db, org, place, day, CLOSED, reason)
    record_audit(
        db,
        org,
        "location.day_closed",
        f"{place.name} closed on {day:%d %b %Y}: {reason}",
        subject_type="location",
        subject_id=place.id,
        location_id=place.id,
        before=previous,
        after={"day": day.isoformat(), "status": CLOSED, "reason": reason},
        ip=ip,
    )
    return row


def reopen_day(
    db: Session, org: OrgContext, location_id: int, day: date, *, ip: str | None = None
) -> bool:
    """Take a closure back: the day is expected again. False when it was not closed."""
    place = _place(db, org, location_id)
    row = mark_of(db, org, place.id, day)
    if row is None or row.status != CLOSED:
        return False
    reason = row.reason
    db.delete(row)
    db.flush()
    record_audit(
        db,
        org,
        "location.day_reopened",
        f"{place.name} open again on {day:%d %b %Y}",
        subject_type="location",
        subject_id=place.id,
        location_id=place.id,
        before={"day": day.isoformat(), "status": CLOSED, "reason": reason},
        after={"day": day.isoformat(), "status": None},
        ip=ip,
    )
    return True
