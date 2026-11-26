"""Square's webhooks (T3.06, ADR-0080; POS-FR-11, DR-21, SEC-11, PERF-07, AC-POS-03, AC-POS-10).

A call must prove it is Square: the signature covers the notification URL and the raw body, and a
call without one is refused and logged without its payload. A verified event is stored once by
its id before anything acts on it, so Square's retries change nothing. The answer comes at once:
an order or catalog change queues a job (the order read, the catalog read) instead of reading
Square in the request. Revocation touches only our own rows, so it is acted on as it arrives.
Square's inventory counts are stored and ignored: they never change stock (C-34, T3.12).
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.access import OrgContext
from app.access.system import connection_for_merchant
from app.config import get_settings
from app.connections import revoked_by_provider
from app.db import get_db
from app.jobs import enqueue
from app.providers.square.normalize import to_event
from app.providers.square.webhooks import (
    CATALOG_VERSION_UPDATED,
    OAUTH_AUTHORIZATION_REVOKED,
    ORDER_EVENTS,
    merchant_id,
    verify_signature,
)
from app.web.inputs import raw_body
from app.webhook_events import processed, record

log = logging.getLogger(__name__)
router = APIRouter()

NOTIFICATION_PATH = "/webhooks/square"
PROVIDER = "square"
# The job each kind of event asks for, and how its copies are merged while it waits.
JOBS = {
    **{kind: ("ingest_orders", "orders") for kind in ORDER_EVENTS},
    CATALOG_VERSION_UPDATED: ("refresh_catalog", "catalog"),
}
ACTED_ON_ARRIVAL = {OAUTH_AUTHORIZATION_REVOKED}


def _tenant_for(db: Session, merchant: str) -> int | None:
    connection = connection_for_merchant(db, PROVIDER, merchant)
    return connection.tenant_id if connection else None


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
        log.warning("rejected a square webhook with a bad signature")  # never its payload
        raise HTTPException(status_code=400, detail="invalid signature")

    try:
        raw = json.loads(body)
        event = to_event(raw)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="not a Square event") from exc
    if not event.event_id:
        raise HTTPException(status_code=400, detail="the event has no id")

    tenant_id = _tenant_for(db, merchant_id(raw))
    job = JOBS.get(event.type)
    wanted = tenant_id is not None and (job is not None or event.type in ACTED_ON_ARRIVAL)
    stored = record(
        db,
        PROVIDER,
        event,
        tenant_id,
        handled_by=job[0] if job else None,
        ignored=not wanted,
    )
    if stored is None:
        db.commit()
        log.info("square event %s delivered again: nothing to do", event.event_id)
        return {"duplicate": True}

    if event.type == OAUTH_AUTHORIZATION_REVOKED:
        # Signed above, so it is Square speaking: the connection is marked revoked and the
        # Owners hear of it (AC-POS-09).
        revoked = revoked_by_provider(db, PROVIDER, merchant_id(raw))
        if revoked is not None:
            processed(db, stored)
        db.commit()
        return {"revoked": revoked is not None}

    if tenant_id is None:
        # A merchant we do not know is not an error worth retrying.
        db.commit()
        log.info("square webhook for unknown merchant %s", merchant_id(raw))
        return {"ignored": "unknown merchant"}

    org = OrgContext.system(tenant_id)
    if job is not None:
        kind, dedupe = job
        enqueue(db, org, kind, dedupe_key=dedupe)
        db.commit()
        return {"queued": kind}

    db.commit()
    return {"ignored": event.type}
