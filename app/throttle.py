"""Throttling (SAAS-A-03, SEC-12): fewer guesses at passwords and links, fewer emails on demand.

Each rule counts attempts by one subject (an account's email or a client's IP address) in a
window. Past the rule's limit the subject is locked out, first for `first_lock`, then twice as
long with every further attempt, up to `max_lock`. Counters are database rows, so every server
counts the same attempts, and their keys are hashes, so no email or address is stored.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Throttle

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window: timedelta
    first_lock: timedelta
    max_lock: timedelta


MINUTE, HOUR = timedelta(minutes=1), timedelta(hours=1)

# Proposed in T1.09 for the owner to confirm (security configuration).
LOGIN_ACCOUNT = Rule("login-account", 5, 15 * MINUTE, MINUTE, HOUR)
LOGIN_IP = Rule("login-ip", 20, 15 * MINUTE, MINUTE, HOUR)
# Emails sent on request: a few an hour per account and per address, so nobody's inbox floods.
RESET_ACCOUNT = Rule("reset-account", 3, HOUR, HOUR, HOUR)
RESET_IP = Rule("reset-ip", 10, HOUR, HOUR, HOUR)
RESEND_ACCOUNT = Rule("resend-account", 3, HOUR, HOUR, HOUR)
# Links that did not work: tokens are unguessable, but nobody gets to keep trying.
LINK_IP = Rule("link-ip", 10, 15 * MINUTE, MINUTE, HOUR)

# A row whose window and lock are this long over is history; the nightly job removes it.
KEEP_FOR = timedelta(days=1)


def client_ip(request: Request) -> str:
    """The caller's address; behind the platform's proxy uvicorn takes it from X-Forwarded-For."""
    return request.client.host if request.client else "unknown"


def _key(rule: Rule, subject: str) -> str:
    return f"{rule.name}:{hashlib.sha256(subject.strip().lower().encode()).hexdigest()}"


def _row(db: Session, rule: Rule, subject: str) -> Throttle | None:
    return db.scalars(select(Throttle).where(Throttle.key == _key(rule, subject))).first()


def locked_for(db: Session, rule: Rule, subject: str) -> timedelta | None:
    """How much longer the subject is locked out, or None when it may try."""
    row = _row(db, rule, subject)
    now = datetime.now(UTC)
    if row is None or row.locked_until is None or row.locked_until <= now:
        return None
    return row.locked_until - now


def hit(db: Session, rule: Rule, subject: str) -> None:
    """Count one attempt; past the limit, lock the subject out for longer each time."""
    now = datetime.now(UTC)
    row = _row(db, rule, subject)
    if row is None:
        row = Throttle(key=_key(rule, subject), attempts=0, window_started_at=now)
        db.add(row)
    elif now - row.window_started_at > rule.window and (
        row.locked_until is None or row.locked_until <= now
    ):
        row.attempts, row.window_started_at, row.locked_until = 0, now, None
    row.attempts += 1
    row.updated_at = now
    over = row.attempts - rule.limit
    if over >= 0:
        lock = min(rule.first_lock * (2**over), rule.max_lock)
        row.locked_until = now + lock
        log.warning("throttle %s locked a subject for %s", rule.name, lock)
    db.flush()


def clear(db: Session, rule: Rule, subject: str) -> None:
    """Forget the subject's attempts: it got in."""
    row = _row(db, rule, subject)
    if row is not None:
        db.delete(row)
        db.flush()


def prune(db: Session, now: datetime | None = None) -> int:
    """Remove counters nobody has touched for a day; their windows and locks are long over."""
    cutoff = (now or datetime.now(UTC)) - KEEP_FOR
    result = db.execute(delete(Throttle).where(Throttle.updated_at < cutoff))
    return int(getattr(result, "rowcount", 0) or 0)


def retry_after(wait: timedelta) -> dict[str, str]:
    """The Retry-After header for a refusal, in whole seconds."""
    return {"Retry-After": str(max(1, int(wait.total_seconds()) + 1))}
