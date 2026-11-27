"""Resend's delivery webhook (ADR-0088): hard bounces and spam complaints suppress the address.

Public and signed like Square's: no session, no CSRF token, the Svix signature is the check, and
nothing is acted on until it holds. An event we do not act on is answered 200 so Resend stops
retrying it; the work is a row or two, so it is done in the request.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.notify.resend import Unverified, apply_event, verify
from app.web.inputs import raw_body

log = logging.getLogger(__name__)
router = APIRouter()

NOTIFICATION_PATH = "/webhooks/resend"


@router.post(NOTIFICATION_PATH)
def resend_webhook(
    request: Request, body: bytes = Depends(raw_body), db: Session = Depends(get_db)
):
    secret = get_settings().resend_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="RESEND_WEBHOOK_SECRET is not set")

    delivery_id = request.headers.get("svix-id", "")
    try:
        verify(
            secret,
            delivery_id,
            request.headers.get("svix-timestamp", ""),
            request.headers.get("svix-signature", ""),
            body,
        )
    except Unverified as exc:
        log.warning("rejected a resend webhook: %s", exc)  # never its payload
        raise HTTPException(status_code=400, detail="invalid signature") from exc
    except ValueError as exc:
        log.error("resend webhook refused: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        event = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="not a Resend event") from exc
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="not a Resend event")

    suppressed = apply_event(db, event, delivery_id)
    db.commit()
    return {"suppressed": suppressed}
