"""Square sync: ingest -> normalize -> load -> derive -> alert."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.config import get_settings
from app.connections import active_connection, credentials, mark_revoked
from app.jobs import enqueue
from app.load.upsert import (
    apply_provider_counts,
    ensure_tracked_stock_items,
    sync_catalog_presence,
    sync_identifiers_from_catalog,
    sync_retail_prices,
    upsert_sold_items,
)
from app.models import (
    Location,
    ProviderConnection,
    StockItem,
    SyncRun,
    SyncStatus,
    Tenant,
    TrackMode,
)
from app.orchestration.alerts import evaluate_and_store
from app.orchestration.subscriptions import apply_location_limit
from app.pos_catalog import store_catalog, store_locations
from app.providers.base import Catalog
from app.providers.square.client import (
    SquareClient,
    SquareError,
    adjustment_change,
    physical_count_change,
)
from app.providers.square.normalize import (
    normalize_catalog,
    normalize_counts,
    to_location,
    to_modifiers,
    to_products,
)
from app.units import from_base

log = logging.getLogger(__name__)

# Square answers 401 once the merchant revoked the authorization (SQ-01).
UNAUTHORIZED = 401


class WriteBackDisabled(RuntimeError):
    """Raised by the push functions while SQUARE_WRITE_BACK is off (C-34)."""


def _require_write_back() -> None:
    # Kept for R1.1's outbox. Until then a call is a bug, so it fails loudly rather than
    # quietly skipping a write the caller believes happened.
    if not get_settings().square_write_back:
        raise WriteBackDisabled("writing to Square is off until R1.1 (C-34)")


def _square_locations(
    db: Session, org: OrgContext, active_only: bool = False
) -> dict[int, tuple[str, str]]:
    """Locations the POS knows about: id -> (its location id, time zone). Owner-added
    locations have no POS id and are never sent to Square."""
    query = select_scoped(org, Location).where(Location.provider_location_id.is_not(None))
    if active_only:
        query = query.where(Location.is_active.is_(True))
    return {
        location.id: (location.provider_location_id, location.timezone)
        for location in db.scalars(query)
        if location.provider_location_id
    }


def _access_token(db: Session, org: OrgContext, connection: ProviderConnection) -> str:
    return credentials(db, org, connection).access_token


def _note_revocation(
    db: Session, org: OrgContext, connection: ProviderConnection, exc: Exception
) -> None:
    if isinstance(exc, SquareError) and exc.status == UNAUTHORIZED:
        mark_revoked(db, org, connection)


def _pull_catalog(
    db: Session, org: OrgContext, connection: ProviderConnection, client: SquareClient
) -> tuple[list[dict], dict]:
    """Locations and catalog: the part of a sync that a catalog change invalidates.

    One read feeds both the external catalog (T3.03) and the legacy rows the sales path still
    reads until T3.12. Square's locations are recorded, never created as the business's own (G11).
    """
    raw_locations = [place for place in client.locations() if place.get("id")]
    raw_objects = client.catalog_objects(("ITEM", "CATEGORY", "MEASUREMENT_UNIT", "MODIFIER_LIST"))
    located = store_locations(db, org, connection, [to_location(p) for p in raw_locations])
    stats: dict[str, int] = {"locations": sum(located.values())}
    stats.update(
        store_catalog(
            db, org, connection, Catalog(to_products(raw_objects), to_modifiers(raw_objects))
        )
    )
    catalog_rows = normalize_catalog(raw_objects)
    stats["sold_items"] = upsert_sold_items(db, org.tenant_id, catalog_rows)
    stats["stock_items_created"] = ensure_tracked_stock_items(db, org.tenant_id, catalog_rows)
    stats["identifiers"] = sync_identifiers_from_catalog(db, org.tenant_id, catalog_rows)
    stats["prices"] = sync_retail_prices(db, org.tenant_id, catalog_rows)
    stats["presence_changed"] = sync_catalog_presence(db, org.tenant_id, catalog_rows)
    return catalog_rows, stats


def sync_catalog(db: Session, org: OrgContext) -> dict:
    """Re-read the catalog alone, which is all a catalog change needs. Safe to repeat."""
    connection = active_connection(db, org)
    run = SyncRun(tenant_id=org.tenant_id, provider=connection.provider, kind="catalog")
    db.add(run)
    db.flush()
    try:
        token = _access_token(db, org, connection)
        with SquareClient(token) as client:
            _rows, stats = _pull_catalog(db, org, connection, client)
    except Exception as exc:
        _note_revocation(db, org, connection, exc)
        run.status = SyncStatus.FAILED
        run.finished_at = datetime.now(UTC)
        run.error = f"{type(exc).__name__}: {exc}"
        db.commit()
        log.exception("catalog sync failed for tenant %s", org.tenant_id)
        raise

    run.status = SyncStatus.SUCCESS
    run.finished_at = datetime.now(UTC)
    run.stats_json = json.dumps(stats, default=str)
    db.flush()
    log.info("catalog sync for tenant %s: %s", org.tenant_id, stats)
    return stats


def run_sync(db: Session, org: OrgContext) -> dict:
    """Full pull for one tenant: catalog and locations, and Square's counts while write-back is
    on. Idempotent, safe to re-run. Sales arrive as order lines (`app/order_ingestion.py`,
    T3.05); this run no longer reads orders."""
    connection = active_connection(db, org)
    run = SyncRun(tenant_id=org.tenant_id, provider=connection.provider, kind="full")
    db.add(run)
    db.flush()

    stats: dict[str, int] = {}
    try:
        token = _access_token(db, org, connection)
        with SquareClient(token) as client:
            catalog_rows, catalog_stats = _pull_catalog(db, org, connection, client)
            stats.update(catalog_stats)

            locations = _square_locations(db, org, active_only=True)
            provider_location_ids = [square_id for square_id, _ in locations.values()]

            if provider_location_ids:
                tracked_ids = [
                    row["provider_item_id"] for row in catalog_rows if row["tracks_inventory"]
                ]
                # Square's counts are read only while write-back is on: they change no stock
                # (C-34), and the scope to read them is not asked for otherwise (SQ-02).
                if tracked_ids and get_settings().square_write_back:
                    stats["counts"] = apply_provider_counts(
                        db,
                        org.tenant_id,
                        normalize_counts(
                            client.inventory_counts(tracked_ids, provider_location_ids)
                        ),
                    )

        # Square may hand back more locations than the plan covers; the extras stay on record.
        tenant = get_scoped(db, org, Tenant, org.tenant_id)
        if tenant is not None:
            stats["locations_set_aside"] = apply_location_limit(db, tenant)

        stats.update(evaluate_and_store(db, org))

        run.status = SyncStatus.SUCCESS
        run.finished_at = datetime.now(UTC)
        run.stats_json = json.dumps(stats, default=str)
        db.flush()
    except Exception as exc:
        _note_revocation(db, org, connection, exc)
        run.status = SyncStatus.FAILED
        run.finished_at = datetime.now(UTC)
        run.error = f"{type(exc).__name__}: {exc}"
        db.flush()
        db.commit()
        log.exception("sync failed for tenant %s", org.tenant_id)
        raise

    db.commit()
    # The digest is a job: a failed send is retried instead of lost (T1.20).
    enqueue(db, org, "send_alert_digest", dedupe_key="digest")
    db.commit()
    return stats


def push_counts_to_square(db: Session, org: OrgContext, stock_item_ids: list[int]) -> int:
    """Send a physical count: someone looked at the shelf, and this is what was on it.

    Only ever call this with an observed quantity. A number the app worked out - a previous
    count plus a delivery, or a derived weight or volume - must go through
    push_adjustments_to_square instead. Sent as a count it would overwrite whatever Square
    holds, including sales Square knows about and we do not.
    """
    _require_write_back()
    if not stock_item_ids:
        return 0

    connection = active_connection(db, org)
    locations = {
        location_id: square_id for location_id, (square_id, _) in _square_locations(db, org).items()
    }
    items = [
        item
        for item in db.scalars(
            select_scoped(org, StockItem).where(StockItem.id.in_(stock_item_ids))
        )
        if item.provider_variation_id and item.track_mode == TrackMode.PROVIDER
    ]
    if not items:
        return 0

    occurred_at = datetime.now(UTC)
    changes = [
        physical_count_change(
            item.provider_variation_id,
            locations[item.location_id],
            str(from_base(item.qty_base, item.stock_unit)),
            occurred_at,
        )
        for item in items
        if item.provider_variation_id and item.location_id in locations
    ]

    token = _access_token(db, org, connection)
    with SquareClient(token) as client:
        client.apply_inventory_changes(changes, str(uuid.uuid4()))

    for item in items:
        item.last_synced_at = occurred_at
    db.flush()
    log.info("pushed %s corrected counts to square for tenant %s", len(changes), org.tenant_id)
    return len(changes)


def push_adjustments_to_square(
    db: Session,
    org: OrgContext,
    deltas: dict[int, Decimal],
    idempotency_key: str | None = None,
) -> int:
    """Add what arrived to Square's count, whatever that count happens to be.

    Receiving is an event, not an observation: the delivery says +24, it does not say the shelf
    now holds 48. An adjustment lands on top of Square's own number, so a sale rung up between
    the delivery and this call survives instead of being overwritten.

    Every receiving path - barcode scan and invoice photo alike - came through here before C-34.
    """
    _require_write_back()
    wanted = {
        item_id: Decimal(delta)
        for item_id, delta in deltas.items()
        if delta is not None and Decimal(delta) > 0
    }
    if not wanted:
        return 0

    locations = {
        location_id: square_id for location_id, (square_id, _) in _square_locations(db, org).items()
    }
    occurred_at = datetime.now(UTC)
    pushing: list[tuple[StockItem, dict]] = []
    for item in db.scalars(select_scoped(org, StockItem).where(StockItem.id.in_(list(wanted)))):
        if item.track_mode is not TrackMode.PROVIDER or not item.provider_variation_id:
            continue
        if item.location_id not in locations:
            continue
        pushing.append(
            (
                item,
                adjustment_change(
                    item.provider_variation_id,
                    locations[item.location_id],
                    str(from_base(wanted[item.id], item.stock_unit)),
                    occurred_at,
                ),
            )
        )
    if not pushing:
        return 0

    connection = active_connection(db, org)
    token = _access_token(db, org, connection)
    with SquareClient(token) as client:
        # A repeated push of the same receipt is dropped by Square rather than added twice.
        client.apply_inventory_changes(
            [change for _item, change in pushing], idempotency_key or str(uuid.uuid4())
        )

    for item, _change in pushing:
        item.last_synced_at = occurred_at
    db.flush()
    log.info("adjusted square stock for %s items of tenant %s", len(pushing), org.tenant_id)
    return len(pushing)


def adjust_stock_in_square(
    db: Session, org: OrgContext, item: StockItem, delta_base: Decimal
) -> bool:
    """Add a delta for one item. Returns False when Square does not own that item's count."""
    return push_adjustments_to_square(db, org, {item.id: delta_base}) > 0


def record_square_failure(db: Session, org: OrgContext, kind: str, exc: Exception) -> None:
    """A Square call outside a sync failed: record it where the dashboard shows sync health.

    The dashboard reads the newest sync run, so the failure is visible there, not only in logs.
    """
    now = datetime.now(UTC)
    db.add(
        SyncRun(
            tenant_id=org.tenant_id,
            kind=kind,
            status=SyncStatus.FAILED,
            started_at=now,
            finished_at=now,
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
    )
