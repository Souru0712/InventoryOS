"""The dashboard's rules (T1.25; CMD-FR-01, -10, -13). Pure.

Money over a period is counted in each location's own business days (ADR-0027): "the last 7
days" at a location is its business today and the six before it, whatever the time zone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

RANGES = ("today", "7d", "30d", "custom")
DEFAULT_RANGE = "7d"
MAX_DAYS = 366
_SPANS = {"today": 1, "7d": 7, "30d": 30}
_LABELS = {"today": "Today", "7d": "Last 7 days", "30d": "Last 30 days"}


class RangeError(ValueError):
    """A date range the dashboard refuses; the default range is shown instead."""


@dataclass(frozen=True)
class DateRange:
    """A period for money figures, resolved at each location against its own today."""

    choice: str = DEFAULT_RANGE
    first: date | None = None  # custom only
    last: date | None = None

    def days_at(self, today: date) -> tuple[date, date]:
        """The first and last business day of the period at a location whose today is `today`."""
        if self.choice == "custom" and self.first is not None and self.last is not None:
            return self.first, self.last
        return today - timedelta(days=_SPANS[self.choice] - 1), today

    @property
    def label(self) -> str:
        if self.choice == "custom" and self.first is not None and self.last is not None:
            return f"{self.first:%d %b %Y} to {self.last:%d %b %Y}"
        return _LABELS[self.choice]


def check_range(choice: str, first: date | None = None, last: date | None = None) -> DateRange:
    """The range the rules accept, or RangeError saying what is wrong with it."""
    if choice not in RANGES:
        raise RangeError("Choose today, 7 days, 30 days or your own dates.")
    if choice != "custom":
        return DateRange(choice)
    if first is None or last is None:
        raise RangeError("Give both a first and a last day.")
    if first > last:
        raise RangeError("The first day comes after the last.")
    if (last - first).days + 1 > MAX_DAYS:
        raise RangeError(f"A range is at most {MAX_DAYS} days.")
    return DateRange("custom", first, last)


def movement_value_cents(qty_base: Decimal, unit_cost_cents: Decimal) -> int:
    """A movement's value at the cost it was posted with, in whole cents, signed as it moved."""
    return int((Decimal(qty_base) * Decimal(unit_cost_cents)).quantize(Decimal("1")))


def completeness_note(units_sold: Decimal, products: int) -> str | None:
    """What the consumption figures are missing (CMD-FR-13), or None when nothing is."""
    if units_sold <= 0 or products <= 0:
        return None
    units = format(units_sold.normalize(), "f")
    things = "product" if products == 1 else "products"
    return (
        f"{units} sold without a recipe in this period ({products} {things}); "
        "ingredient use reads low until they have one."
    )
