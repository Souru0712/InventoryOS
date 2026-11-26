"""Provider webhook events, stored once and processed later (T3.06, ADR-0080; DR-21, SEC-11).

The only writer of `webhook_events`. An event is stored by (provider, event id) before anything
acts on it, so a repeated delivery is acknowledged and changes nothing (POS-FR-11). One a job
acts on stays `received` until that job succeeds; a failed job leaves it received, where the
health page (T3.09) can count it. Events belong to no organization until their merchant names
one, so this module reads them directly, as `app/notify/email.py` reads suppressions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import WebhookEvent
from app.providers.base import NormalizedEvent

RECEIVED, PROCESSED, IGNORED = "received", "processed", "ignored"


def record(
    db: Session,
    provider: str,
    event: NormalizedEvent,
    tenant_id: int | None,
    *,
    handled_by: str | None = None,
    ignored: bool = False,
) -> WebhookEvent | None:
    """Store a verified event once; None when it was received before (a repeated delivery)."""
    try:
        with db.begin_nested():
            row = WebhookEvent(
                provider=provider,
                event_id=event.event_id[:120],
                event_type=event.type[:80],
                tenant_id=tenant_id,
                status=IGNORED if ignored else RECEIVED,
                handled_by=handled_by,
                entity_ids=json.dumps(list(event.entity_ids)),
                processed_at=datetime.now(UTC) if ignored else None,
            )
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        return None


def processed(db: Session, row: WebhookEvent, now: datetime | None = None) -> None:
    row.status, row.processed_at = PROCESSED, now or datetime.now(UTC)
    db.flush()


def processed_by(db: Session, tenant_id: int, job: str, started: datetime) -> int:
    """A job that succeeded acted on every event it was queued for: those received for the
    business before it started. How many."""
    rows = list(
        db.scalars(
            select(WebhookEvent).where(
                WebhookEvent.tenant_id == tenant_id,
                WebhookEvent.handled_by == job,
                WebhookEvent.status == RECEIVED,
                WebhookEvent.received_at <= started,
            )
        )
    )
    now = datetime.now(UTC)
    for row in rows:
        row.status, row.processed_at = PROCESSED, now
    db.flush()
    return len(rows)


def waiting(db: Session, tenant_id: int, received_before: datetime) -> int:
    """A business's events received before an instant and still not acted on (POS-FR-27)."""
    return len(
        list(
            db.scalars(
                select(WebhookEvent.id).where(
                    WebhookEvent.tenant_id == tenant_id,
                    WebhookEvent.status == RECEIVED,
                    WebhookEvent.received_at < received_before,
                )
            )
        )
    )
