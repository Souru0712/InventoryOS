"""Supplier rules (T2.10; PRC-FR-01..03, -08, AC-PRC-01, -02, ADR-0061). Pure.

A supplier sells items in its own purchase units: a case of 6 x 1 L is 6000 ml of the item. A
price is per purchase unit; a new price far from the last one is worth a look (PRC-FR-08).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ORDERING_METHODS = ("email", "phone", "portal", "rep", "other")
METHOD_LABELS = {
    "email": "Email",
    "phone": "Phone",
    "portal": "Online portal",
    "rep": "Sales rep visits",
    "other": "Other",
}
# A new price at least this far from the last one opens a price_variance exception.
PRICE_CHANGE_PERCENT = Decimal(10)
ZERO = Decimal(0)


class SupplierRuleError(ValueError):
    """A supplier change the rules refuse; nothing is written."""


def clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise SupplierRuleError("A supplier needs a name.")
    if len(cleaned) > 200:
        raise SupplierRuleError("Keep the name under 200 characters.")
    return cleaned


def check_lead_time(days: int) -> int:
    if not 0 <= days <= 90:
        raise SupplierRuleError("Lead time is 0 to 90 days.")
    return days


def check_pack(pack_base: Decimal) -> Decimal:
    if pack_base <= ZERO:
        raise SupplierRuleError("A pack holds more than nothing.")
    return pack_base


def in_base(purchase_qty: Decimal, pack_base: Decimal) -> Decimal:
    """How much of the item a quantity of purchase units is (AC-PRC-02)."""
    return (purchase_qty * pack_base).quantize(Decimal("0.0001"))


@dataclass(frozen=True)
class PriceChange:
    before_cents: int
    after_cents: int

    @property
    def percent(self) -> Decimal:
        return ((Decimal(self.after_cents) - self.before_cents) * 100 / self.before_cents).quantize(
            Decimal("0.1")
        )

    @property
    def notable(self) -> bool:
        """Far enough from the last price to be worth a look (PRC-FR-08)."""
        return abs(self.percent) >= PRICE_CHANGE_PERCENT


def price_change(last_cents: int | None, new_cents: int) -> PriceChange | None:
    """The change from the last price, if there was one to compare with."""
    if new_cents < 0:
        raise SupplierRuleError("A price cannot be below zero.")
    if not last_cents:
        return None
    return PriceChange(last_cents, new_cents)
