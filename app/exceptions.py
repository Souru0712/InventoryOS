"""Exceptions: raising them, keeping them current, and people acting on them (T1.19, ADR-0043).

The only writer of the exceptions table. An exception is open at most once per type and subject:
a recurring condition updates it (REC-FR-13), a condition that clears is resolved by the system
(REC-FR-14), and a person closes one only by fixing what it is about or dismissing it with a
reason (AC-REC-08). Acknowledging and dismissing are conditional status changes (DR-07) recorded
in the business history. Nothing here posts or edits a quantity.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped, update_scoped
from app.access.permissions import VIEW_COSTS
from app.audit import record_audit
from app.models import ExceptionRecord, Location, StockItem
from app.transform.exceptions import (
    OPEN_STATUSES,
    TYPES,
    ExceptionRuleError,
    Finding,
    Key,
    check_dismissal,
    check_finding,
    rank,
    reconcile,
    severity_of,
    visible_to,
)

log = logging.getLogger(__name__)

CLEARED = "The condition cleared"


class NotFound(LookupError):
    """No such exception that org may see."""


@dataclass(frozen=True)
class ExceptionView:
    """One exception as a member may see it: no amount for a role without cost access."""

    id: int
    type: str
    label: str
    severity: str
    status: str
    title: str
    fix: str
    subject_type: str
    subject_id: str
    subject_name: str
    location_name: str
    money_impact_cents: int | None
    occurrences: int
    detected_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    resolution: str | None
    resolution_note: str | None


def _open(db: Session, org: OrgContext, key: Key, lock: bool = False) -> ExceptionRecord | None:
    exception_type, subject_type, subject_id = key
    query = select_scoped(org, ExceptionRecord).where(
        ExceptionRecord.type == exception_type,
        ExceptionRecord.subject_type == subject_type,
        ExceptionRecord.subject_id == subject_id,
        ExceptionRecord.status.in_(OPEN_STATUSES),
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalars(query).first()


def _refresh(row: ExceptionRecord, finding: Finding, now: datetime) -> None:
    """What the condition looks like now; who acknowledged it stays."""
    row.title = finding.title[:500]
    row.severity = severity_of(finding)
    row.money_impact_cents = finding.money_impact_cents
    row.quantity_base = finding.quantity_base
    row.days_of_cover = finding.days_of_cover
    if finding.location_id is not None:
        row.location_id = finding.location_id
    row.last_seen_at = now


def _insert(
    db: Session, org: OrgContext, finding: Finding, now: datetime, occurrences: int
) -> ExceptionRecord | None:
    """A new open exception; None when another run opened the same one a moment ago."""
    try:
        with db.begin_nested():
            row = ExceptionRecord(
                tenant_id=org.tenant_id,
                location_id=finding.location_id,
                type=finding.type,
                severity=severity_of(finding),
                subject_type=finding.subject_type,
                subject_id=finding.subject_id,
                title=finding.title[:500],
                money_impact_cents=finding.money_impact_cents,
                quantity_base=finding.quantity_base,
                days_of_cover=finding.days_of_cover,
                occurrences=occurrences,
                detected_at=now,
                last_seen_at=now,
            )
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        return None


def record_exception(
    db: Session,
    org: OrgContext,
    finding: Finding,
    *,
    occurrences: int = 1,
    now: datetime | None = None,
) -> ExceptionRecord:
    """Report a problem seen as an event: it adds to the open exception for its type and
    subject, or opens one (REC-FR-13, AC-REC-06)."""
    check_finding(finding)
    if occurrences < 1:
        raise ExceptionRuleError("an occurrence is at least one")
    now = now or datetime.now(UTC)
    for _attempt in range(3):
        current = _open(db, org, finding.key, lock=True)
        if current is not None:
            current.occurrences += occurrences
            _refresh(current, finding, now)
            db.flush()
            return current
        row = _insert(db, org, finding, now, occurrences)
        if row is not None:
            return row
    raise RuntimeError(f"could not record {finding.key} after three tries")


def apply_findings(
    db: Session,
    org: OrgContext,
    findings: Iterable[Finding],
    answers_for: frozenset[str],
    now: datetime | None = None,
) -> dict[str, int]:
    """One evaluation run: open what is new, refresh what is still there, and resolve, as the
    system, the open exceptions of the types it answers for that it no longer finds."""
    now = now or datetime.now(UTC)
    found = list(findings)
    held = {
        (row.type, row.subject_type, row.subject_id): row
        for row in db.scalars(
            select_scoped(org, ExceptionRecord).where(
                ExceptionRecord.status.in_(OPEN_STATUSES),
                ExceptionRecord.type.in_(sorted(answers_for)),
            )
        )
    }
    plan = reconcile(held, found, answers_for)
    opened = 0
    for finding in plan.opened:
        if _insert(db, org, finding, now, 1) is not None:
            opened += 1
        elif (row := _open(db, org, finding.key, lock=True)) is not None:
            _refresh(row, finding, now)
    for finding in plan.refreshed:
        _refresh(held[finding.key], finding, now)
    resolved = close_cleared(db, org, [held[key].id for key in plan.cleared], now)
    db.flush()
    return {"open": len(plan.opened) + len(plan.refreshed), "opened": opened, "resolved": resolved}


def close_cleared(db: Session, org: OrgContext, ids: Sequence[int], now: datetime) -> int:
    """Resolve, as the system, exceptions whose condition has cleared (AC-REC-07)."""
    if not ids:
        return 0
    result = db.execute(
        update_scoped(org, ExceptionRecord)
        .where(ExceptionRecord.id.in_(ids), ExceptionRecord.status.in_(OPEN_STATUSES))
        .values(
            status="resolved",
            resolution="system",
            resolved_at=now,
            resolved_by_user_id=None,
            resolution_note=CLEARED,
        )
        .execution_options(synchronize_session="fetch")
    )
    return int(getattr(result, "rowcount", 0) or 0)


def clear(db: Session, org: OrgContext, key: Key, now: datetime | None = None) -> bool:
    """Resolve, as the system, the open exception for this type and subject, if there is one:
    its condition has cleared (a job of that kind succeeded, say)."""
    row = _open(db, org, key)
    if row is None:
        return False
    return close_cleared(db, org, [row.id], now or datetime.now(UTC)) == 1


def _visible(db: Session, org: OrgContext, exception_id: int) -> ExceptionRecord:
    row = get_scoped(db, org, ExceptionRecord, exception_id)
    if row is None or not visible_to(row.type, org.can(VIEW_COSTS)):
        raise NotFound("exception not found")
    return row


def _transition(
    db: Session, org: OrgContext, row: ExceptionRecord, wanted_from: Sequence[str], **values
) -> None:
    """Change the status only if it still is what the person saw (DR-07)."""
    result = db.execute(
        update_scoped(org, ExceptionRecord)
        .where(ExceptionRecord.id == row.id, ExceptionRecord.status.in_(wanted_from))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        raise ExceptionRuleError("Someone has already dealt with it.")
    db.refresh(row)


def acknowledge(
    db: Session, org: OrgContext, exception_id: int, ip: str | None = None
) -> ExceptionRecord:
    """Say someone has seen it and is on it; it stays open until fixed or dismissed."""
    row = _visible(db, org, exception_id)
    _transition(
        db,
        org,
        row,
        ("open",),
        status="acknowledged",
        acknowledged_at=datetime.now(UTC),
        acknowledged_by_user_id=org.user_id,
    )
    record_audit(
        db,
        org,
        "exception.acknowledged",
        f"Acknowledged: {row.title}",
        subject_type="exception",
        subject_id=row.id,
        location_id=row.location_id,
        ip=ip,
    )
    return row


def dismiss(
    db: Session, org: OrgContext, exception_id: int, reason: str | None, ip: str | None = None
) -> ExceptionRecord:
    """Close it without a fix, saying why (AC-REC-08). The quantity is not touched."""
    note = check_dismissal(reason)
    row = _visible(db, org, exception_id)
    _transition(
        db,
        org,
        row,
        OPEN_STATUSES,
        status="dismissed",
        resolution="dismissed",
        resolution_note=note,
        resolved_at=datetime.now(UTC),
        resolved_by_user_id=org.user_id,
    )
    record_audit(
        db,
        org,
        "exception.dismissed",
        f"Dismissed: {row.title}",
        subject_type="exception",
        subject_id=row.id,
        location_id=row.location_id,
        after={"reason": note},
        ip=ip,
    )
    return row


def mark_notified(rows: Iterable[ExceptionRecord], now: datetime) -> None:
    """The digest has considered these; the next one raises only what is new."""
    for row in rows:
        row.notified_at = now


def listing(db: Session, org: OrgContext, *, closed: bool = False) -> list[ExceptionView]:
    """Open exceptions (or the latest closed ones) org may see, most severe first; financial
    ones and every amount withheld from a role without cost access (AC-REC-09)."""
    costs = org.can(VIEW_COSTS)
    query = select_scoped(org, ExceptionRecord)
    if closed:
        query = (
            query.where(ExceptionRecord.status.not_in(OPEN_STATUSES))
            .order_by(ExceptionRecord.resolved_at.desc())
            .limit(200)
        )
    else:
        query = query.where(ExceptionRecord.status.in_(OPEN_STATUSES))
    rows = [row for row in db.scalars(query) if visible_to(row.type, costs)]
    places = {place.id: place.name for place in db.scalars(select_scoped(org, Location))}
    stock_ids = [int(row.subject_id) for row in rows if row.subject_type == "stock_item"]
    names = {
        item.id: item.name
        for item in db.scalars(select_scoped(org, StockItem).where(StockItem.id.in_(stock_ids)))
    }
    views = [
        ExceptionView(
            id=row.id,
            type=row.type,
            label=TYPES[row.type].label,
            severity=row.severity,
            status=row.status,
            title=row.title,
            fix=TYPES[row.type].fix,
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            subject_name=(
                names.get(int(row.subject_id), "")
                if row.subject_type == "stock_item"
                else row.subject_type.replace("_", " ")
            ),
            location_name=places.get(row.location_id, "") if row.location_id else "",
            money_impact_cents=row.money_impact_cents if costs else None,
            occurrences=row.occurrences,
            detected_at=row.detected_at,
            last_seen_at=row.last_seen_at,
            resolved_at=row.resolved_at,
            resolution=row.resolution,
            resolution_note=row.resolution_note,
        )
        for row in rows
    ]
    if closed:
        return views
    return sorted(views, key=lambda view: (rank(view.severity), -view.last_seen_at.timestamp()))
