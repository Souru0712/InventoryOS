"""What a POS reports, and which location tracks each of its locations (T3.03, ADR-0077;
POS-FR-03, POS-FR-04, SQ-05..09).

The only writer of `external_locations`, `external_products` and `external_modifiers`.
Provider-neutral: it stores the adapter contract's normalized types. A catalog or location list is
the POS's full current listing: what it no longer lists is marked inactive, never deleted, so a
mapping and its history stay (SQ-08). A re-sync of the same listing changes nothing.

Tracking is the business's own choice (G11): a POS location is tracked by one of the business's
locations, which then carries its id (`provider_location_id`); the sync never creates a location.
Only existing, active locations can track one, so the count stays within the plan.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.audit import record_audit
from app.connections import ACTIVE, credentials, mark_revoked, name_of
from app.models import (
    Base,
    ExternalLocation,
    ExternalModifier,
    ExternalProduct,
    Location,
    ProviderConnection,
    SyncRun,
    SyncStatus,
)
from app.providers import adapter_for
from app.providers.base import AdapterError, Catalog, NormalizedLocation

Row = TypeVar("Row", bound=Base)


class MappingError(ValueError):
    """A tracking change the rules refuse; nothing is written."""


class NotFound(LookupError):
    """No such POS location in this business."""


@dataclass(frozen=True)
class Summary:
    """What the business holds from its POS (AC-POS-01's import summary)."""

    items: int
    variations: int
    modifiers: int
    locations: int
    tracked: int


def _upsert(
    db: Session,
    org: OrgContext,
    model: type[Row],
    connection: ProviderConnection,
    key: str,
    listed: Mapping[str, dict[str, Any]],
    now: datetime,
) -> dict[str, int]:
    """Make the connection's rows of `model` match the listing, by external id."""
    existing = {
        getattr(row, key): row
        for row in db.scalars(
            select_scoped(org, model).where(model.connection_id == connection.id)  # type: ignore[attr-defined]
        )
    }
    stats = {"created": 0, "changed": 0, "deactivated": 0}
    for external_id, fields in listed.items():
        row = existing.get(external_id)
        if row is None:
            db.add(
                model(
                    tenant_id=org.tenant_id,
                    connection_id=connection.id,
                    seen_at=now,
                    **{key: external_id},
                    **fields,
                )
            )
            stats["created"] += 1
            continue
        if any(getattr(row, name) != value for name, value in fields.items()):
            for name, value in fields.items():
                setattr(row, name, value)
            stats["changed"] += 1
        row.seen_at = now  # type: ignore[attr-defined]
    for external_id, row in existing.items():
        if external_id not in listed and row.active:  # type: ignore[attr-defined]
            row.active = False  # type: ignore[attr-defined]
            stats["deactivated"] += 1
    db.flush()
    return stats


def store_locations(
    db: Session,
    org: OrgContext,
    connection: ProviderConnection,
    locations: Iterable[NormalizedLocation],
    now: datetime | None = None,
) -> dict[str, int]:
    """The POS's current location list, as the business's record of it."""
    listed = {
        place.external_location_id: {
            "name": place.name,
            "timezone": place.timezone,
            "address": place.address,
            "active": place.active,
        }
        for place in locations
    }
    return _upsert(
        db,
        org,
        ExternalLocation,
        connection,
        "external_location_id",
        listed,
        now or datetime.now(UTC),
    )


def store_catalog(
    db: Session,
    org: OrgContext,
    connection: ProviderConnection,
    catalog: Catalog,
    now: datetime | None = None,
) -> dict[str, int]:
    """The POS's current catalog: one row per variation, one per modifier (POS-FR-04)."""
    now = now or datetime.now(UTC)
    products = _upsert(
        db,
        org,
        ExternalProduct,
        connection,
        "external_variation_id",
        {
            p.external_variation_id: {
                "external_item_id": p.external_item_id,
                "name": p.name,
                "variation_name": p.variation_name,
                "category": p.category,
                "price_cents": p.price_cents,
                "unit": p.unit,
                "active": p.active,
            }
            for p in catalog.products
        },
        now,
    )
    modifiers = _upsert(
        db,
        org,
        ExternalModifier,
        connection,
        "external_modifier_id",
        {
            m.external_modifier_id: {"list_name": m.list_name, "name": m.name, "active": True}
            for m in catalog.modifiers
        },
        now,
    )
    return {f"products_{k}": v for k, v in products.items()} | {
        f"modifiers_{k}": v for k, v in modifiers.items()
    }


def external_locations(
    db: Session, org: OrgContext, connection: ProviderConnection
) -> list[tuple[ExternalLocation, Location | None]]:
    """Each POS location, open ones first, with the business's location that tracks it."""
    places = list(
        db.scalars(
            select_scoped(org, ExternalLocation)
            .where(ExternalLocation.connection_id == connection.id)
            .order_by(ExternalLocation.active.desc(), ExternalLocation.name)
        )
    )
    trackers = {
        location.provider_location_id: location
        for location in db.scalars(
            select_scoped(org, Location).where(Location.provider == connection.provider)
        )
    }
    return [(place, trackers.get(place.external_location_id)) for place in places]


def summary(db: Session, org: OrgContext, connection: ProviderConnection) -> Summary:
    def active(model: type[Row]) -> Sequence[Row]:
        return list(
            db.scalars(
                select_scoped(org, model).where(
                    model.connection_id == connection.id,  # type: ignore[attr-defined]
                    model.active.is_(True),  # type: ignore[attr-defined]
                )
            )
        )

    products = active(ExternalProduct)
    places = external_locations(db, org, connection)
    return Summary(
        items=len({p.external_item_id or p.external_variation_id for p in products}),
        variations=len(products),
        modifiers=len(active(ExternalModifier)),
        locations=len([place for place, _ in places if place.active]),
        tracked=len([1 for _, tracker in places if tracker is not None]),
    )


def track_location(
    db: Session,
    org: OrgContext,
    external_location_id: int,
    location_id: int | None,
    *,
    ip: str | None = None,
) -> bool:
    """Track a POS location with one of the business's locations, or with none (POS-FR-03).
    False when nothing changed. A location tracks one POS location at most."""
    place = get_scoped(db, org, ExternalLocation, external_location_id)
    if place is None:
        raise NotFound("no such POS location")
    connection = get_scoped(db, org, ProviderConnection, place.connection_id)
    if connection is None or connection.status != ACTIVE:
        raise MappingError("Reconnect before changing which locations are tracked.")
    provider = connection.provider
    current = db.scalars(
        select_scoped(org, Location).where(
            Location.provider == provider,
            Location.provider_location_id == place.external_location_id,
        )
    ).first()

    chosen: Location | None = None
    if location_id is not None:
        chosen = get_scoped(db, org, Location, location_id)
        if chosen is None:
            raise NotFound("no such location")
        if chosen is current:
            return False
        if chosen.archived_at is not None or not chosen.is_active:
            raise MappingError(f"{chosen.name} is not in use; restore it first.")
        if chosen.provider_location_id is not None:
            raise MappingError(
                f"{chosen.name} already tracks another location; stop tracking that one first."
            )
    elif current is None:
        return False

    before = current.name if current else None
    if current is not None:
        current.provider = None
        current.provider_location_id = None
        db.flush()  # the unique (business, provider, POS location) must be free first
    if chosen is not None:
        chosen.provider = provider
        chosen.provider_location_id = place.external_location_id
    db.flush()

    name = name_of(provider)
    affected = chosen or current
    record_audit(
        db,
        org,
        "connection.location_tracked" if chosen else "connection.location_untracked",
        f"{name} location {place.name} tracked by {chosen.name}"
        if chosen
        else f"Stopped tracking the {name} location {place.name}",
        subject_type="external_location",
        subject_id=place.id,
        location_id=affected.id if affected else None,
        before={"tracked_by": before},
        after={"tracked_by": chosen.name if chosen else None},
        ip=ip,
    )
    return True


# --- a catalog change, read as a job (T3.06) ------------------------------------------------------

# Square sends an event per edit; a burst is one read.
CATALOG_DEBOUNCE = timedelta(seconds=60)


def read_recently(db: Session, org: OrgContext, now: datetime | None = None) -> bool:
    last = db.scalars(
        select_scoped(org, SyncRun)
        .where(SyncRun.kind == "catalog")
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    ).first()
    now = now or datetime.now(UTC)
    return bool(last and now - last.started_at < CATALOG_DEBOUNCE)


def refresh_catalog(
    db: Session, org: OrgContext, now: datetime | None = None, *, force: bool = False
) -> dict[str, int]:
    """Read each active connection's locations and catalog through its adapter: as a catalog
    change asks (SQ-07), hourly as a job, and at once when someone asks (`force`). A burst of
    events reads once. A revoked authorization is marked, not retried; a passing failure raises
    so the job tries again."""
    now = now or datetime.now(UTC)
    if not force and read_recently(db, org, now):
        return {"skipped": 1}
    totals: dict[str, int] = {}
    for connection in db.scalars(
        select_scoped(org, ProviderConnection).where(ProviderConnection.status == ACTIVE)
    ):
        held = credentials(db, org, connection, now)
        run = SyncRun(tenant_id=org.tenant_id, provider=connection.provider, kind="catalog")
        db.add(run)
        db.flush()
        adapter = adapter_for(connection.provider)
        try:
            places = adapter.list_locations(held)
            catalog = adapter.sync_catalog(held)
        except AdapterError as exc:
            run.status, run.finished_at, run.error = SyncStatus.FAILED, now, str(exc)[:500]
            if not exc.revoked:
                db.flush()
                raise
            mark_revoked(db, org, connection, now=now)
            totals["revoked"] = totals.get("revoked", 0) + 1
            continue
        stats = {"locations": sum(store_locations(db, org, connection, places, now).values())}
        stats |= store_catalog(db, org, connection, catalog, now)
        run.status, run.finished_at, run.stats_json = SyncStatus.SUCCESS, now, json.dumps(stats)
        db.flush()
        for key, value in stats.items():
            totals[key] = totals.get(key, 0) + value
    return totals
