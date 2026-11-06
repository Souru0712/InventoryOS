"""Square calls us when stock moves at the till, so our numbers do not go stale between syncs."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.load.upsert import apply_provider_counts
from app.models import Location, ProviderConnection, StockItem, SyncRun, TrackMode
from app.orchestration.sync import NotConnected, sync_catalog
from app.providers.square.webhooks import (
    CATALOG_VERSION_UPDATED,
    INVENTORY_COUNT_UPDATED,
    inventory_counts,
    merchant_id,
    verify_signature,
)
from app.transform.square_normalize import normalize_counts
from app.web.inputs import raw_body

log = logging.getLogger(__name__)
router = APIRouter()

NOTIFICATION_PATH = "/webhooks/square"
# Editing a catalog sends an event per change; one pull covers a burst of them.
CATALOG_DEBOUNCE = timedelta(seconds=60)


def _tenant_for(db: Session, merchant: str) -> int | None:
    connection = db.scalars(
        select(ProviderConnection).where(
            ProviderConnection.provider == "square",
            ProviderConnection.merchant_id == merchant,
            ProviderConnection.status == "active",
        )
    ).first()
    return connection.tenant_id if connection else None


def _drop_stale(db: Session, tenant_id: int, rows: list[dict]) -> list[dict]:
    """Square warns that offline tills deliver counts out of order; older news never wins.

    last_synced_at is when we last read this item, so a count calculated before that is
    already reflected in what we hold.
    """
    locations = {
        loc.provider_location_id: loc.id
        for loc in db.scalars(select(Location).where(Location.tenant_id == tenant_id))
    }
    items = {
        (item.location_id, item.provider_variation_id): item
        for item in db.scalars(
            select(StockItem).where(
                StockItem.tenant_id == tenant_id, StockItem.track_mode == TrackMode.PROVIDER
            )
        )
        if item.provider_variation_id
    }

    fresh = []
    for row in rows:
        location_id = locations.get(row["provider_location_id"])
        item = items.get((location_id, row["provider_item_id"])) if location_id else None
        if item is None:
            continue
        calculated_at = _parse(row.get("calculated_at"))
        last_seen = item.last_synced_at
        if calculated_at and last_seen and calculated_at < last_seen:
            log.info("ignored a stale count for item %s", item.id)
            continue
        fresh.append(row)
    return fresh


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Square's stamps carry an offset; one without is read as UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _catalog_read_recently(db: Session, tenant_id: int) -> bool:
    last = db.scalars(
        select(SyncRun)
        .where(SyncRun.tenant_id == tenant_id, SyncRun.kind == "catalog")
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    ).first()
    started = last.started_at if last else None
    return bool(started and datetime.now(UTC) - started < CATALOG_DEBOUNCE)


@router.post(NOTIFICATION_PATH)
def square_webhook(
    request: Request, body: bytes = Depends(raw_body), db: Session = Depends(get_db)
):
    settings = get_settings()
    key = settings.square_webhook_signature_key
    if not key:
        raise HTTPException(status_code=503, detail="SQUARE_WEBHOOK_SIGNATURE_KEY is not set")

    signature = request.headers.get("x-square-hmacsha256-signature", "")
    url = settings.app_base_url.rstrip("/") + NOTIFICATION_PATH
    if not verify_signature(url, body, signature, key):
        log.warning("rejected a square webhook with a bad signature")
        raise HTTPException(status_code=400, detail="invalid signature")

    try:
        event = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc

    if event.get("type") == CATALOG_VERSION_UPDATED:
        tenant_id = _tenant_for(db, merchant_id(event))
        if tenant_id is None:
            log.info("square webhook for unknown merchant %s", merchant_id(event))
            return {"ignored": "unknown merchant"}
        # The event carries only a version number, so the catalog itself has to be re-read.
        if _catalog_read_recently(db, tenant_id):
            return {"ignored": "catalog was just read"}
        try:
            stats = sync_catalog(db, tenant_id)
        except NotConnected:
            log.info("catalog event for tenant %s, which is not connected", tenant_id)
            return {"ignored": "not connected"}
        db.commit()
        log.info("catalog event re-read the catalog for tenant %s: %s", tenant_id, stats)
        return {"catalog": stats}

    if event.get("type") != INVENTORY_COUNT_UPDATED:
        return {"ignored": event.get("type")}

    tenant_id = _tenant_for(db, merchant_id(event))
    if tenant_id is None:
        # A merchant we do not know is not an error worth retrying.
        log.info("square webhook for unknown merchant %s", merchant_id(event))
        return {"ignored": "unknown merchant"}

    rows = _drop_stale(db, tenant_id, normalize_counts(inventory_counts(event)))
    updated = apply_provider_counts(db, tenant_id, rows) if rows else 0
    db.commit()
    log.info("square webhook updated %s counts for tenant %s", updated, tenant_id)
    return {"updated": updated}
