"""When a lapsed business's retention ends, and when a deletion falls due (T4.06, C-44). Pure.

A business that stops paying keeps read-only access for the retention period, its POS ingestion
still running (PD-07); then it is told, and deleted after the same cancellable window an Owner's
own request gets (SAAS-D-03, SAAS-D-04). Read-only starts when the trial ended, when the paid
period ended after cancelling or pausing, or when the grace after a failed payment ran out.
"""

from __future__ import annotations

from datetime import datetime, timedelta

READ_ONLY_STATUSES = ("expired", "canceled", "paused", "past_due")
OWNER, RETENTION = "owner", "retention"
DELETION_REASONS = (OWNER, RETENTION)


def read_only_since(
    status: str,
    *,
    trial_ends_at: datetime | None,
    current_period_end: datetime | None,
    past_due_since: datetime | None,
    last_event_at: datetime | None,
    grace: timedelta,
) -> datetime | None:
    """When the business became read-only; None while it has full access or cannot be told."""
    if status == "expired":
        return trial_ends_at or current_period_end or last_event_at
    if status in ("canceled", "paused"):
        return current_period_end or last_event_at
    if status == "past_due":
        return past_due_since + grace if past_due_since else None
    return None


def retention_over(since: datetime | None, now: datetime, retention: timedelta) -> bool:
    """Whether a read-only business has had its retention period."""
    return since is not None and now >= since + retention


def due(requested_at: datetime, window: timedelta) -> datetime:
    """When a deletion asked for now happens, unless cancelled first."""
    return requested_at + window
