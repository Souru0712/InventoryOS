"""Switching a location between Standalone and Connected at a cutover business date (T3.10,
ADR-0084; WF-25, POS-FR-19, STD-FR-11, DR-31, AC-SAL-06).

No re-setup and no data migration: one engine. From the cutover date D, the location's sales come
from the chosen source and from it alone. The old source stops the day before; its counted sales
from D are voided, giving back what they took, after a preview. Sales the new source already
recorded from D (kept but not counted until now) start counting. Earlier days keep their history.
Connected needs a location tracking a POS location of an active connection. The default D for
leaving a POS is the day after its last day with sales. Audited. The plan following the mode is
T4.02's (PD-39).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.audit import record_audit
from app.connections import current, name_of
from app.counts import last_count_at
from app.models import Location, SalesTransaction
from app.sales import Cutover, cut_over, cutover_plan, source_of_record
from app.transform.business_days import business_date

CONNECTED, STANDALONE = "connected", "standalone"
# What a Standalone location takes its sales from (STD-FR-02, STD-FR-03).
HAND_KINDS = ("manual", "file_import")
REACH = timedelta(days=30)  # a cutover within 30 days of today, either way (PD-50)
LABELS = {"manual": "hand entry", "file_import": "sales file imports"}


def label(kind: str, provider: str | None) -> str:
    return name_of(provider) if kind == "pos_adapter" and provider else LABELS.get(kind, kind)


class ModeRuleError(ValueError):
    """A mode change the rules refuse; nothing is written."""


class NotFound(LookupError):
    """No such location in this business."""


@dataclass(frozen=True)
class Preview:
    location: Location
    current: str  # the source of record's kind today, or "" when there is none
    kind: str  # what the sales come from after the cutover
    provider: str | None
    cutover: date
    today: date
    plan: Cutover
    last_count: date | None = None  # a switch never reaches back past it (C-53)

    @property
    def earliest(self) -> date:
        return max(self.today - REACH, self.last_count or self.today - REACH)

    @property
    def latest(self) -> date:
        return self.today + REACH


def _location(db: Session, org: OrgContext, location_id: int) -> Location:
    place = get_scoped(db, org, Location, location_id)
    if place is None:
        raise NotFound("no such location")
    return place


def today_at(place: Location, now: datetime | None = None) -> date:
    return business_date(now or datetime.now(UTC), place.timezone, place.business_day_cutoff_hour)


def default_cutover(db: Session, org: OrgContext, place: Location, kind: str) -> date:
    """Today for joining a POS; for leaving one, the day after its last day with sales (WF-25)."""
    today = today_at(place)
    if kind in HAND_KINDS:
        record = source_of_record(db, org, place.id, today)
        if record is not None and record.kind == "pos_adapter":
            last = db.scalar(
                select_scoped(org, SalesTransaction)
                .with_only_columns(func.max(SalesTransaction.business_date))
                .where(
                    SalesTransaction.sales_source_id == record.id,
                    SalesTransaction.status == "completed",
                )
            )
            if last is not None:
                return min(last + timedelta(days=1), today)
    return today


def _check(
    db: Session,
    org: OrgContext,
    place: Location,
    kind: str,
    provider: str | None,
    cutover: date,
    today: date,
) -> None:
    if kind != "pos_adapter" and kind not in HAND_KINDS:
        raise ModeRuleError("Choose where the sales come from.")
    if abs((cutover - today).days) > REACH.days:
        raise ModeRuleError(f"Choose a date within {REACH.days} days of today.")
    if kind == "pos_adapter":
        connection = current(db, org, provider or "")
        if connection is None or connection.status != "active":
            raise ModeRuleError(f"Connect {name_of(provider or '')} first.")
        if place.provider != provider or not place.provider_location_id:
            raise ModeRuleError(
                f"Choose which {name_of(provider or '')} location {place.name} tracks first."
            )
    record = source_of_record(db, org, place.id, cutover)
    if (
        record is not None
        and record.kind == kind
        and (kind != "pos_adapter" or record.provider == provider)
        and record.active_from <= cutover
    ):
        raise ModeRuleError(
            f"{place.name} already takes its sales from {label(kind, provider)} on "
            f"{cutover:%d %b %Y}."
        )


def preview(
    db: Session,
    org: OrgContext,
    location_id: int,
    kind: str,
    provider: str | None = None,
    cutover: date | None = None,
) -> Preview:
    place = _location(db, org, location_id)
    provider = provider if kind == "pos_adapter" else None
    today = today_at(place)
    counted = last_count_at(db, org, place.id)
    last_count = (
        business_date(counted, place.timezone, place.business_day_cutoff_hour) if counted else None
    )
    cutover = cutover or max(default_cutover(db, org, place, kind), last_count or date.min)
    _check(db, org, place, kind, provider, cutover, today)
    plan = cutover_plan(db, org, place.id, kind, provider, cutover)
    if counted is not None and last_count is not None:
        _check_count(place, cutover, plan, counted, last_count)
    record = source_of_record(db, org, place.id, today)
    return Preview(
        place, record.kind if record else "", kind, provider, cutover, today, plan, last_count
    )


def _check_count(
    place: Location, cutover: date, plan: Cutover, counted: datetime, last_count: date
) -> None:
    """Counts are checkpoints (C-53): a switch may not change a sale from before the latest one,
    nor start before its business day. Older days are corrected by counts and adjustments."""
    if cutover < last_count or (plan.first_moment is not None and plan.first_moment < counted):
        raise ModeRuleError(
            f"{place.name} was counted on {last_count:%d %b %Y}, and a switch never reaches back "
            "past a count. Choose a later date, or correct earlier days with a count or an "
            "adjustment."
        )


def switch(
    db: Session,
    org: OrgContext,
    location_id: int,
    kind: str,
    provider: str | None,
    cutover: date,
    *,
    ip: str | None = None,
) -> Preview:
    """Confirm a preview: the location's sales come from `kind` from `cutover` on (WF-25)."""
    shown = preview(db, org, location_id, kind, provider, cutover)
    source = label(kind, shown.provider)
    cut_over(
        db,
        org,
        shown.location.id,
        kind,
        shown.provider,
        cutover,
        f"Sales of {shown.location.name} come from {source} from {cutover:%d %b %Y}",
    )
    record_audit(
        db,
        org,
        "settings.sales_mode",
        f"{shown.location.name} takes its sales from {source} from {cutover:%d %b %Y}",
        subject_type="location",
        subject_id=shown.location.id,
        location_id=shown.location.id,
        before={"source": shown.current or None},
        after={
            "source": kind,
            "provider": shown.provider,
            "cutover": cutover.isoformat(),
            "voided": len(shown.plan.voided),
            "counted": len(shown.plan.counted),
        },
        ip=ip,
    )
    return shown


def mode(db: Session, org: OrgContext, location_id: int) -> str:
    """Connected or Standalone today, by the location's source of record."""
    place = _location(db, org, location_id)
    record = source_of_record(db, org, place.id, today_at(place))
    return CONNECTED if record is not None and record.kind == "pos_adapter" else STANDALONE
