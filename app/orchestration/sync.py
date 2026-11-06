"""Square sync: ingest -> normalize -> load -> derive -> alert."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.load.upsert import (
    apply_provider_counts,
    ensure_tracked_stock_items,
    sync_catalog_presence,
    sync_identifiers_from_catalog,
    sync_retail_prices,
    upsert_locations,
    upsert_sales_daily,
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
from app.orchestration.alerts import evaluate_and_store, send_alert_digest
from app.orchestration.subscriptions import apply_location_limit
from app.orchestration.views import refresh_derived_quantities
from app.providers.square.client import (
    SquareClient,
    adjustment_change,
    physical_count_change,
)
from app.providers.square.oauth import decrypt, encrypt, refresh
from app.transform.square_normalize import (
    daily_sales_from_orders,
    normalize_catalog,
    normalize_counts,
    normalize_locations,
)
from app.units import from_base

log = logging.getLogger(__name__)

SALES_LOOKBACK_DAYS = 60
REFRESH_MARGIN = timedelta(days=7)


class NotConnected(RuntimeError):
    pass


class WriteBackDisabled(RuntimeError):
    """Raised by the push functions while SQUARE_WRITE_BACK is off (C-34)."""


def _require_write_back() -> None:
    # Kept for R1.1's outbox. Until then a call is a bug, so it fails loudly rather than
    # quietly skipping a write the caller believes happened.
    if not get_settings().square_write_back:
        raise WriteBackDisabled("writing to Square is off until R1.1 (C-34)")


def active_connection(db: Session, tenant_id: int, provider: str = "square") -> ProviderConnection:
    connection = db.scalars(
        select(ProviderConnection).where(
            ProviderConnection.tenant_id == tenant_id,
            ProviderConnection.provider == provider,
            ProviderConnection.status == "active",
        )
    ).first()
    if connection is None:
        raise NotConnected(f"tenant {tenant_id} has no active {provider} connection")
    return connection


def _access_token(db: Session, connection: ProviderConnection) -> str:
    expires_at = connection.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if expires_at and expires_at - REFRESH_MARGIN < datetime.now(UTC):
        if not connection.refresh_token_enc:
            raise NotConnected(
                f"square token for tenant {connection.tenant_id} expires at {expires_at} "
                "and no refresh token is stored; the merchant must reconnect"
            )
        log.info("refreshing square token for tenant %s", connection.tenant_id)
        token = refresh(decrypt(connection.refresh_token_enc))
        connection.access_token_enc = encrypt(token["access_token"])
        if token["refresh_token"]:
            connection.refresh_token_enc = encrypt(token["refresh_token"])
        connection.expires_at = token["expires_at"]
        db.flush()
    return decrypt(connection.access_token_enc)


def _pull_catalog(db: Session, tenant_id: int, client: SquareClient) -> tuple[list[dict], dict]:
    """Locations and catalog: the part of a sync that a catalog change invalidates."""
    stats: dict[str, int] = {
        "locations": upsert_locations(db, tenant_id, normalize_locations(client.locations()))
    }
    catalog_rows = normalize_catalog(
        client.catalog_objects(("ITEM", "CATEGORY", "MEASUREMENT_UNIT"))
    )
    stats["sold_items"] = upsert_sold_items(db, tenant_id, catalog_rows)
    stats["stock_items_created"] = ensure_tracked_stock_items(db, tenant_id, catalog_rows)
    stats["identifiers"] = sync_identifiers_from_catalog(db, tenant_id, catalog_rows)
    stats["prices"] = sync_retail_prices(db, tenant_id, catalog_rows)
    stats["presence_changed"] = sync_catalog_presence(db, tenant_id, catalog_rows)
    return catalog_rows, stats


def sync_catalog(db: Session, tenant_id: int) -> dict:
    """Re-read the catalog alone, which is all a catalog change needs. Safe to repeat."""
    connection = active_connection(db, tenant_id)
    run = SyncRun(tenant_id=tenant_id, provider=connection.provider, kind="catalog")
    db.add(run)
    db.flush()
    try:
        token = _access_token(db, connection)
        with SquareClient(token) as client:
            _rows, stats = _pull_catalog(db, tenant_id, client)
    except Exception as exc:
        run.status = SyncStatus.FAILED
        run.finished_at = datetime.now(UTC)
        run.error = f"{type(exc).__name__}: {exc}"
        db.commit()
        log.exception("catalog sync failed for tenant %s", tenant_id)
        raise

    run.status = SyncStatus.SUCCESS
    run.finished_at = datetime.now(UTC)
    run.stats_json = json.dumps(stats, default=str)
    db.flush()
    log.info("catalog sync for tenant %s: %s", tenant_id, stats)
    return stats


def run_sync(db: Session, tenant_id: int, days_back: int = SALES_LOOKBACK_DAYS) -> dict:
    """Full pull for one tenant. Idempotent, safe to re-run.

    This is the reconciliation pass: webhooks keep counts current between runs, and this run
    is what makes them right again if one was missed, dropped, or arrived out of order.
    """
    connection = active_connection(db, tenant_id)
    run = SyncRun(tenant_id=tenant_id, provider=connection.provider, kind="full")
    db.add(run)
    db.flush()

    stats: dict[str, int] = {}
    try:
        token = _access_token(db, connection)
        with SquareClient(token) as client:
            catalog_rows, catalog_stats = _pull_catalog(db, tenant_id, client)
            stats.update(catalog_stats)

            locations = list(
                db.scalars(
                    select(Location).where(
                        Location.tenant_id == tenant_id, Location.is_active.is_(True)
                    )
                )
            )
            provider_location_ids = [loc.provider_location_id for loc in locations]
            timezones = {loc.provider_location_id: loc.timezone for loc in locations}

            if provider_location_ids:
                tracked_ids = [
                    row["provider_item_id"] for row in catalog_rows if row["tracks_inventory"]
                ]
                if tracked_ids:
                    stats["counts"] = apply_provider_counts(
                        db,
                        tenant_id,
                        normalize_counts(
                            client.inventory_counts(tracked_ids, provider_location_ids)
                        ),
                    )

                end = datetime.now(UTC)
                orders = client.orders(provider_location_ids, end - timedelta(days=days_back), end)
                stats["sales_rows"] = upsert_sales_daily(
                    db, tenant_id, daily_sales_from_orders(orders, timezones)
                )

        # Square may hand back more locations than the plan covers; the extras stay on record.
        tenant = db.get(Tenant, tenant_id)
        if tenant is not None:
            stats["locations_set_aside"] = apply_location_limit(db, tenant)

        stats["derived_updated"] = refresh_derived_quantities(db, tenant_id)
        stats.update(evaluate_and_store(db, tenant_id))

        run.status = SyncStatus.SUCCESS
        run.finished_at = datetime.now(UTC)
        run.stats_json = json.dumps(stats, default=str)
        db.flush()
    except Exception as exc:
        run.status = SyncStatus.FAILED
        run.finished_at = datetime.now(UTC)
        run.error = f"{type(exc).__name__}: {exc}"
        db.flush()
        db.commit()
        log.exception("sync failed for tenant %s", tenant_id)
        raise

    db.commit()
    try:
        send_alert_digest(db, tenant_id)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("alert digest failed for tenant %s after a successful sync", tenant_id)

    return stats


def push_counts_to_square(db: Session, tenant_id: int, stock_item_ids: list[int]) -> int:
    """Send a physical count: someone looked at the shelf, and this is what was on it.

    Only ever call this with an observed quantity. A number the app worked out - a previous
    count plus a delivery, or a derived weight or volume - must go through
    push_adjustments_to_square instead. Sent as a count it would overwrite whatever Square
    holds, including sales Square knows about and we do not.
    """
    _require_write_back()
    if not stock_item_ids:
        return 0

    connection = active_connection(db, tenant_id)
    locations = {
        loc.id: loc.provider_location_id
        for loc in db.scalars(select(Location).where(Location.tenant_id == tenant_id))
    }
    items = [
        item
        for item in db.scalars(
            select(StockItem).where(
                StockItem.tenant_id == tenant_id, StockItem.id.in_(stock_item_ids)
            )
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

    token = _access_token(db, connection)
    with SquareClient(token) as client:
        client.apply_inventory_changes(changes, str(uuid.uuid4()))

    for item in items:
        item.last_synced_at = occurred_at
    db.flush()
    log.info("pushed %s corrected counts to square for tenant %s", len(changes), tenant_id)
    return len(changes)


def push_adjustments_to_square(
    db: Session,
    tenant_id: int,
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
        loc.id: loc.provider_location_id
        for loc in db.scalars(select(Location).where(Location.tenant_id == tenant_id))
    }
    occurred_at = datetime.now(UTC)
    pushing: list[tuple[StockItem, dict]] = []
    for item in db.scalars(
        select(StockItem).where(StockItem.tenant_id == tenant_id, StockItem.id.in_(list(wanted)))
    ):
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

    connection = active_connection(db, tenant_id)
    token = _access_token(db, connection)
    with SquareClient(token) as client:
        # A repeated push of the same receipt is dropped by Square rather than added twice.
        client.apply_inventory_changes(
            [change for _item, change in pushing], idempotency_key or str(uuid.uuid4())
        )

    for item, _change in pushing:
        item.last_synced_at = occurred_at
    db.flush()
    log.info("adjusted square stock for %s items of tenant %s", len(pushing), tenant_id)
    return len(pushing)


def adjust_stock_in_square(
    db: Session, tenant_id: int, item: StockItem, delta_base: Decimal
) -> bool:
    """Add a delta for one item. Returns False when Square does not own that item's count."""
    return push_adjustments_to_square(db, tenant_id, {item.id: delta_base}) > 0


def record_square_failure(db: Session, tenant_id: int, kind: str, exc: Exception) -> None:
    """A Square call outside a sync failed: record it where the dashboard shows sync health.

    The dashboard reads the newest sync run, so the failure is visible there, not only in logs.
    """
    now = datetime.now(UTC)
    db.add(
        SyncRun(
            tenant_id=tenant_id,
            kind=kind,
            status=SyncStatus.FAILED,
            started_at=now,
            finished_at=now,
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
    )


def refresh_item_from_square(db: Session, tenant_id: int, item: StockItem) -> bool:
    """Re-read one variation's count, so a scan shows what Square holds right now."""
    if item.track_mode is not TrackMode.PROVIDER or not item.provider_variation_id:
        return False
    location = db.get(Location, item.location_id)
    if location is None:
        return False

    connection = active_connection(db, tenant_id)
    token = _access_token(db, connection)
    with SquareClient(token) as client:
        rows = normalize_counts(
            client.inventory_counts([item.provider_variation_id], [location.provider_location_id])
        )
    return bool(apply_provider_counts(db, tenant_id, rows))
