"""Business days at a location: which day an instant belongs to (DR-20, A-08), and open days."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
EVERY_DAY = 0b1111111


def business_date(moment: datetime, time_zone: str, cutoff_hour: int = 0) -> date:
    """The business day an instant falls in: its local date, or the day before when it is
    earlier than the cutoff, so a sale at 01:30 after a late close counts for the evening."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("an instant needs a time zone")
    if not 0 <= cutoff_hour <= 23:
        raise ValueError(f"cutoff hour {cutoff_hour} is not between 0 and 23")
    local = moment.astimezone(ZoneInfo(time_zone))
    return local.date() - timedelta(days=1) if local.hour < cutoff_hour else local.date()


def business_day_end(day: date, time_zone: str, cutoff_hour: int = 0) -> datetime:
    """The last instant of a business day: just before the next day's cutoff, local time. An
    "as of" figure for that day counts everything up to and including it."""
    if not 0 <= cutoff_hour <= 23:
        raise ValueError(f"cutoff hour {cutoff_hour} is not between 0 and 23")
    zone = ZoneInfo(time_zone)
    next_start = datetime(day.year, day.month, day.day, cutoff_hour, tzinfo=zone) + timedelta(
        days=1
    )
    return next_start - timedelta(microseconds=1)


@lru_cache(maxsize=1)
def time_zones() -> frozenset[str]:
    """Every IANA time zone name this machine knows."""
    return frozenset(available_timezones())


def is_time_zone(name: str) -> bool:
    return name in time_zones()


def open_days_mask(days: Iterable[str]) -> int:
    """('mon', 'sat') -> a mask with Monday as bit 0. Unknown names are refused."""
    mask = 0
    for day in days:
        if day not in WEEKDAYS:
            raise ValueError(f"{day!r} is not a day of the week")
        mask |= 1 << WEEKDAYS.index(day)
    return mask


def open_day_names(mask: int) -> tuple[str, ...]:
    if not 0 <= mask <= EVERY_DAY:
        raise ValueError(f"{mask} is not a set of weekdays")
    return tuple(day for index, day in enumerate(WEEKDAYS) if mask >> index & 1)
