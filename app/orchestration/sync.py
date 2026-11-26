"""Bringing a POS connection up to date on request, and the write-back kept for R1.1.

The legacy hourly sync is gone (T3.12, ADR-0085): the catalog and locations arrive through the
adapter (`app/pos_catalog.py`), orders through `app/order_ingestion.py`, both as jobs.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.config import get_settings
from app.connections import active_connection, credentials
from app.models import Location, ProviderConnection, StockItem, SyncRun, SyncStatus, TrackMode
from app.pos_catalog import refresh_catalog
from app.providers.base import AdapterError
from app.providers.square.client import SquareClient, adjustment_change, physical_count_change
from app.units import from_base

log = logging.getLogger(__name__)


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


def run_sync(db: Session, org: OrgContext) -> dict[str, int]:
    """Bring the connection up to date now (after connecting, "Sync now", `cli sync`): its
    locations and catalog read through the adapter at once rather than debounced (T3.03).
    Orders are read by their own job (T3.05); exceptions and the digest by the evaluation job.
    Raises NotConnected without an active connection; a failed read is kept as a failed run."""
    active_connection(db, org)
    try:
        stats = refresh_catalog(db, org, force=True)
    except AdapterError:
        db.commit()  # the failed run stays on record for the sync history
        log.exception("catalog read failed for tenant %s", org.tenant_id)
        raise
    db.commit()
    log.info("synced tenant %s: %s", org.tenant_id, stats)
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
