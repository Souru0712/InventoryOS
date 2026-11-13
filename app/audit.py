"""The business audit history (T1.12; DR-23, SEC-14, ADM-FR-11): who changed what, and when.

Every change to settings, locations, items, the team, connections and the plan calls
record_audit().
Events are append-only: this module only ever inserts them, and the scoped layer refuses to
update them. Managers see the history of their locations; the business-wide history (settings,
team, connections, billing) is for roles that manage the business.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import OrgContext
from app.access.scoped import scope_filter
from app.models import AuditEvent, User

# The kinds of change the history can be filtered by: the part of an action before the dot.
CATEGORIES = {
    "settings": "Business settings",
    "location": "Locations",
    "team": "Team",
    "item": "Items and categories",
    "product": "Products and modifiers",
    "connection": "Connections",
    "billing": "Plan and billing",
    "exception": "Exceptions",
}
PAGE = 100


def changes(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict, dict]:
    """Only the fields that differ, as (was, became)."""
    keys = [key for key in after if before.get(key) != after.get(key)]
    return {key: before.get(key) for key in keys}, {key: after.get(key) for key in keys}


def record_audit(
    db: Session,
    org: OrgContext,
    action: str,
    summary: str,
    *,
    subject_type: str,
    subject_id: int | None = None,
    location_id: int | None = None,
    before: dict | None = None,
    after: dict | None = None,
    ip: str | None = None,
    actor_user_id: int | None = None,
) -> AuditEvent:
    """Add one event to org's history, by org's member unless actor_user_id says otherwise
    (no member at all means the product did it: a webhook or a scheduled job)."""
    if action.split(".", 1)[0] not in CATEGORIES:
        raise ValueError(f"unknown audit category in {action!r}")
    actor = actor_user_id if actor_user_id is not None else org.user_id
    event = AuditEvent(
        tenant_id=org.tenant_id,
        location_id=location_id,
        actor_kind="user" if actor is not None else "system",
        actor_user_id=actor,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        summary=summary[:500],
        before=before or None,
        after=after or None,
        ip=ip,
    )
    db.add(event)
    db.flush()
    return event


def history(
    db: Session,
    org: OrgContext,
    *,
    category: str = "",
    person: int | None = None,
    before: datetime | None = None,
) -> list[tuple[AuditEvent, str | None]]:
    """The newest events org may see, with each actor's email, filtered; a page at a time."""
    query = (
        select(AuditEvent, User.email)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .where(*scope_filter(org, AuditEvent))
    )
    if not org.can("settings.manage"):
        query = query.where(AuditEvent.location_id.is_not(None))
    if category in CATEGORIES:
        query = query.where(AuditEvent.action.startswith(f"{category}."))
    if person is not None:
        query = query.where(AuditEvent.actor_user_id == person)
    if before is not None:
        query = query.where(AuditEvent.at < before)
    rows = db.execute(query.order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).limit(PAGE))
    return [(event, email) for event, email in rows]
