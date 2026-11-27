"""T4.06: when read-only began and when retention ends (C-44, SAAS-D-03). Pure."""

from datetime import UTC, datetime, timedelta

from app.transform.retention import due, read_only_since, retention_over

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
GRACE = timedelta(days=7)


def since(status, **fields):
    defaults = {
        "trial_ends_at": None,
        "current_period_end": None,
        "past_due_since": None,
        "last_event_at": None,
    }
    return read_only_since(status, grace=GRACE, **(defaults | fields))


def test_read_only_starts_where_access_ended():
    ended = NOW - timedelta(days=10)
    assert since("expired", trial_ends_at=ended) == ended
    assert since("canceled", current_period_end=ended) == ended
    assert since("paused", last_event_at=ended) == ended
    assert since("past_due", past_due_since=ended) == ended + GRACE


def test_a_business_with_full_access_has_no_retention_clock():
    assert since("active", current_period_end=NOW) is None
    assert since("trialing", trial_ends_at=NOW) is None
    assert since("canceled") is None, "nothing to count from: never deleted by guess"


def test_retention_ends_after_its_days_and_the_deletion_after_the_window():
    began = NOW - timedelta(days=90)
    assert retention_over(began, NOW, timedelta(days=90)) is True
    assert retention_over(began + timedelta(seconds=1), NOW, timedelta(days=90)) is False
    assert retention_over(None, NOW, timedelta(days=90)) is False
    assert due(NOW, timedelta(days=7)) == NOW + timedelta(days=7)
