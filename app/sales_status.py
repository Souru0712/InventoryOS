"""Sales-source status, per location (T3.09; CMD-FR-08, POS-FR-27, AC-CMD-05, AC-CMD-06).

One place to see whether sales are arriving. A location tracking a POS location of a connection
that is active or revoked is Connected: its status is that connection's health. Any other is
Standalone: the last business day with sales recorded and the open days still missing them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.connections import current
from app.daily_sales import missing_days
from app.models import Location, ProviderConnection, SalesTransaction, SyncRun, SyncStatus
from app.sales import unmapped_units
from app.transform.integration_health import EVENT_DELAY, ConnectionHealth, Reads
from app.webhook_events import waiting

PROVIDERS = ("square",)


@dataclass(frozen=True)
class LocationStatus:
    location_id: int
    name: str
    mode: str  # connected | standalone
    health: ConnectionHealth | None = None
    last_sales_day: date | None = None
    missing: tuple[date, ...] = ()


def _reads(db: Session, org: OrgContext, provider: str, now: datetime) -> Reads:
    runs = select_scoped(org, SyncRun).where(SyncRun.provider == provider, SyncRun.kind == "orders")

    def last(status: SyncStatus) -> datetime | None:
        row = db.scalars(
            runs.where(SyncRun.status == status).order_by(SyncRun.started_at.desc()).limit(1)
        ).first()
        return (row.finished_at or row.started_at) if row else None

    day = [r.status for r in db.scalars(runs.where(SyncRun.started_at >= now - timedelta(days=1)))]
    return Reads(
        last_success=last(SyncStatus.SUCCESS),
        last_failure=last(SyncStatus.FAILED),
        runs_24h=len(day),
        failures_24h=sum(1 for s in day if s is SyncStatus.FAILED),
    )


def connection_health(
    db: Session, org: OrgContext, connection: ProviderConnection, now: datetime
) -> ConnectionHealth:
    tracked = db.scalar(
        select_scoped(org, Location)
        .with_only_columns(func.count())
        .where(
            Location.provider == connection.provider,
            Location.provider_location_id.is_not(None),
            Location.is_active.is_(True),
        )
    )
    unmapped = unmapped_units(db, org, connection.provider)
    return ConnectionHealth(
        provider=connection.provider,
        status=connection.status,
        tracked=tracked or 0,
        reads=_reads(db, org, connection.provider, now),
        waiting_events=waiting(db, org.tenant_id, now - EVENT_DELAY),
        token_expires_at=connection.expires_at,
        unmapped=len([ref for ref, units in unmapped.items() if units]),
        now=now,
    )


def _last_sales_day(db: Session, org: OrgContext, location_id: int) -> date | None:
    return db.scalar(
        select_scoped(org, SalesTransaction)
        .with_only_columns(func.max(SalesTransaction.business_date))
        .where(
            SalesTransaction.location_id == location_id,
            SalesTransaction.status == "completed",
            SalesTransaction.of_record.is_(True),
        )
    )


def sales_status(
    db: Session, org: OrgContext, now: datetime, location_id: int | None = None
) -> list[LocationStatus]:
    """Each active location's sales source and whether its sales are arriving."""
    healths: dict[str, ConnectionHealth] = {}
    for provider in PROVIDERS:
        connection = current(db, org, provider)
        if connection is not None and connection.status in ("active", "revoked"):
            healths[provider] = connection_health(db, org, connection, now)
    missing: dict[int, list[date]] = {}
    for finding in missing_days(db, org, now):
        if finding.location_id is not None:
            missing.setdefault(finding.location_id, []).append(
                date.fromisoformat(finding.subject_id.split(":", 1)[1])
            )
    query = select_scoped(org, Location).where(Location.is_active.is_(True)).order_by(Location.name)
    if location_id is not None:
        query = query.where(Location.id == location_id)
    found = []
    for place in db.scalars(query):
        health = healths.get(place.provider or "") if place.provider_location_id else None
        if health is not None:
            found.append(LocationStatus(place.id, place.name, "connected", health=health))
        else:
            found.append(
                LocationStatus(
                    place.id,
                    place.name,
                    "standalone",
                    last_sales_day=_last_sales_day(db, org, place.id),
                    missing=tuple(sorted(missing.get(place.id, []))),
                )
            )
    return found
