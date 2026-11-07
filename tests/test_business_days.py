"""Business days: local dates with a cutoff, across time zones and daylight-saving changes."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.transform.business_days import (
    EVERY_DAY,
    business_date,
    is_time_zone,
    open_day_names,
    open_days_mask,
)

LA = "America/Los_Angeles"


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def test_the_local_date_not_the_utc_date():
    # 03:00 UTC on the 6th is 19:00 on the 5th in Los Angeles (PST, UTC-8).
    assert business_date(utc(2026, 11, 6, 3, 0), LA) == date(2026, 11, 5)
    assert business_date(utc(2026, 11, 6, 3, 0), "UTC") == date(2026, 11, 6)
    assert business_date(utc(2026, 11, 6, 3, 0), "Asia/Tokyo") == date(2026, 11, 6)


def test_before_the_cutoff_belongs_to_the_day_before():
    # A bar closing at 2 a.m. with a 4 a.m. cutoff: 01:30 and 03:59 are still Friday.
    friday = date(2026, 11, 6)
    assert business_date(utc(2026, 11, 7, 9, 30), LA, cutoff_hour=4) == friday  # 01:30 PST
    assert business_date(utc(2026, 11, 7, 11, 59), LA, cutoff_hour=4) == friday  # 03:59 PST
    assert business_date(utc(2026, 11, 7, 12, 0), LA, cutoff_hour=4) == friday + timedelta(1)


def test_midnight_cutoff_is_the_calendar_day():
    assert business_date(utc(2026, 11, 7, 8, 0), LA) == date(2026, 11, 7)  # 00:00 PST
    assert business_date(utc(2026, 11, 7, 7, 59), LA) == date(2026, 11, 6)


def test_spring_forward_skips_an_hour_without_losing_the_day():
    # 8 March 2026: 02:00 PST jumps to 03:00 PDT. 03:30 PDT is 10:30 UTC.
    assert business_date(utc(2026, 3, 8, 10, 30), LA, cutoff_hour=3) == date(2026, 3, 8)
    # 01:30 PST (09:30 UTC) is before a 3 a.m. cutoff: still the 7th.
    assert business_date(utc(2026, 3, 8, 9, 30), LA, cutoff_hour=3) == date(2026, 3, 7)


def test_fall_back_repeats_an_hour_inside_the_same_business_day():
    # 1 November 2026: 01:00-02:00 happens twice. Both 01:30s are before a 2 a.m. cutoff.
    first = utc(2026, 11, 1, 8, 30)  # 01:30 PDT
    second = utc(2026, 11, 1, 9, 30)  # 01:30 PST
    assert business_date(first, LA, 2) == business_date(second, LA, 2) == date(2026, 10, 31)
    assert business_date(utc(2026, 11, 1, 10, 0), LA, 2) == date(2026, 11, 1)  # 02:00 PST


def test_any_offset_in_gives_the_same_answer():
    same_instant = datetime(2026, 11, 6, 12, 0, tzinfo=timezone(timedelta(hours=9)))
    assert business_date(same_instant, LA) == business_date(utc(2026, 11, 6, 3, 0), LA)


def test_a_naive_instant_is_refused():
    with pytest.raises(ValueError, match="time zone"):
        business_date(datetime(2026, 11, 6, 3, 0), LA)  # naive on purpose


@pytest.mark.parametrize("cutoff", [-1, 24])
def test_a_cutoff_outside_the_day_is_refused(cutoff):
    with pytest.raises(ValueError, match="cutoff"):
        business_date(utc(2026, 11, 6, 3, 0), LA, cutoff)


def test_time_zone_names_are_iana_names():
    assert is_time_zone(LA) and is_time_zone("UTC") and is_time_zone("Europe/London")
    assert not is_time_zone("Mars/Olympus_Mons")
    assert not is_time_zone("")


def test_open_days_round_trip():
    assert open_days_mask(["mon", "tue", "wed", "thu", "fri"]) == 0b0011111
    assert open_day_names(0b1100000) == ("sat", "sun")
    assert open_day_names(EVERY_DAY) == ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    assert open_day_names(open_days_mask(["sun", "mon"])) == ("mon", "sun")
    with pytest.raises(ValueError):
        open_days_mask(["funday"])
    with pytest.raises(ValueError):
        open_day_names(128)
