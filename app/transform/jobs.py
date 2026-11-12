"""Background jobs' rules (T1.20; ID-06, REL-07, SCL-03). Pure.

A job is one piece of work for one organization, run at least once: a handler must be safe to
run again. A failure is retried after an exponentially growing wait; after the last attempt the
job is dead-lettered and becomes an exception someone can see.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

STATUSES = ("pending", "running", "done", "dead")


@dataclass(frozen=True)
class JobKind:
    label: str
    max_attempts: int = 5


KINDS: Mapping[str, JobKind] = {
    "evaluate_exceptions": JobKind("Checking stock and expiry"),
    "send_alert_digest": JobKind("Sending the alert email"),
    "send_weekly_report": JobKind("Sending the weekly report"),
    "extract_document": JobKind("Reading an invoice", max_attempts=3),
    "verify_ledger": JobKind("Checking the ledger", max_attempts=3),
}
JOB_KINDS = tuple(KINDS)

FIRST_RETRY = timedelta(minutes=1)
LONGEST_WAIT = timedelta(hours=1)
# A job running this long was abandoned by a worker that died; it is run again.
ABANDONED_AFTER = timedelta(minutes=15)


class JobRuleError(ValueError):
    """A job the rules refuse; nothing is queued."""


def check_kind(kind: str) -> JobKind:
    found = KINDS.get(kind)
    if found is None:
        raise JobRuleError(f"{kind!r} is not a kind of job")
    return found


def backoff(attempts: int) -> timedelta:
    """How long to wait after the given number of failed attempts: 1, 2, 4, 8... minutes, at
    most an hour."""
    if attempts < 1:
        return timedelta(0)
    wait: timedelta = FIRST_RETRY * (2 ** min(attempts - 1, 12))
    return wait if wait < LONGEST_WAIT else LONGEST_WAIT


@dataclass(frozen=True)
class AfterFailure:
    dead: bool
    run_at: datetime | None  # when to try again; None once dead


def after_failure(attempts: int, max_attempts: int, now: datetime) -> AfterFailure:
    """Retry later, or give up: the attempt just made counts."""
    if attempts >= max_attempts:
        return AfterFailure(dead=True, run_at=None)
    return AfterFailure(dead=False, run_at=now + backoff(attempts))
