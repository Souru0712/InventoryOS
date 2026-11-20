"""Business settings rules (T2.19; ADM-FR-01, -03..06). Pure."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.transform.products import PRODUCTION_MODES

UNIT_SYSTEMS = ("metric", "us")
SYSTEM_LABELS = {"metric": "Metric (g, kg, ml, L)", "us": "US (oz, lb, fl oz, gal)"}
# The units each system offers first when someone picks how an item is counted.
SYSTEM_UNITS = {
    "metric": ("g", "kg", "ml", "l"),
    "us": ("oz", "lb", "floz", "cup", "pt", "qt", "gal"),
}
DEFAULT_COUNT_REVIEW_CENTS = 2500
DEFAULT_COUNT_REVIEW_PERCENT = 20
DEFAULT_ADJUSTMENT_REVIEW_CENTS = 5000


class SettingsRuleError(ValueError):
    """A setting that cannot be saved, with the reason to show."""


def check_schedule(day: int, hour: int) -> tuple[int, int]:
    """The weekly summary's day (Monday is 0) and hour, on the business's clock."""
    if not 0 <= day <= 6:
        raise SettingsRuleError("Choose a day of the week.")
    if not 0 <= hour <= 23:
        raise SettingsRuleError("Choose an hour from 0 to 23.")
    return day, hour


@dataclass(frozen=True)
class Alerts:
    expiry_alert_days: int
    digest_hour: int | None  # None: the alert email goes out as soon as something is found
    count_review_cents: int
    count_review_percent: int
    adjustment_review_cents: int


def _money_cents(raw: str, what: str) -> int:
    try:
        amount = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise SettingsRuleError(f"{what} is an amount like 25 or 12.50.") from exc
    if not amount.is_finite() or amount < 0 or amount > 1_000_000:
        raise SettingsRuleError(f"{what} is between 0 and 1,000,000.")
    return int((amount * 100).quantize(Decimal("1")))


def check_alerts(
    expiry_alert_days: str,
    digest_hour: str,
    count_review: str,
    count_review_percent: str,
    adjustment_review: str,
) -> Alerts:
    """ADM-FR-05: the expiry window, the alert email's hour and the review thresholds."""
    try:
        days = int(expiry_alert_days.strip())
        percent = int(count_review_percent.strip())
        hour = int(digest_hour.strip()) if digest_hour.strip() else None
    except ValueError as exc:
        raise SettingsRuleError("Days, hours and percentages are whole numbers.") from exc
    if not 0 <= days <= 60:
        raise SettingsRuleError("Warn between 0 and 60 days before a date.")
    if hour is not None and not 0 <= hour <= 23:
        raise SettingsRuleError("Choose an hour from 0 to 23, or as soon as found.")
    if not 1 <= percent <= 100:
        raise SettingsRuleError("A count difference share is between 1 and 100 %.")
    return Alerts(
        days,
        hour,
        _money_cents(count_review, "A count difference worth reviewing"),
        percent,
        _money_cents(adjustment_review, "An adjustment worth reviewing"),
    )


def check_defaults(unit_system: str, production_mode: str) -> tuple[str, str]:
    """ADM-FR-03 and -06: the units offered first, and how new products are made."""
    if unit_system not in UNIT_SYSTEMS:
        raise SettingsRuleError("Choose metric or US units.")
    if production_mode not in PRODUCTION_MODES:
        raise SettingsRuleError("Choose how new products are made.")
    return unit_system, production_mode


def ordered_units(codes: Sequence[str], system: str) -> list[str]:
    """The units of one kind, the business's own system first, in their usual order."""
    first = [c for c in SYSTEM_UNITS.get(system, ()) if c in codes]
    return first + [c for c in codes if c not in first]
