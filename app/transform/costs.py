"""Cost basis and valuation (T1.24; ANL-FR-01, -02, INV-FR-07, -24, DR-19, PD-08). Pure.

The cost basis rule: a historical figure uses the unit cost snapshotted on each movement when it
was posted, so it never changes; the current valuation uses each item's current cost, the latest
accepted receipt's unless someone overrode it. Value is Σ max(recorded, 0) × cost: stock below
zero is a count to do, not negative money. An item held with no cost is listed, not hidden.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

COST_SOURCES = ("opening", "receipt", "override", "migration")
BASIS = "latest receipt cost, unless overridden"
ZERO = Decimal("0")


class CostRuleError(ValueError):
    """A cost the rules refuse; nothing is written."""


def check_cost(cost: Decimal) -> Decimal:
    """A unit cost in cents per base unit, kept to four places; never below zero (DR-19)."""
    if cost < 0:
        raise CostRuleError("A cost cannot be below zero.")
    return Decimal(cost).quantize(Decimal("0.0001"))


@dataclass(frozen=True)
class Holding:
    """One item at one location to value."""

    location_id: int
    inventory_item_id: int
    name: str
    qty_base: Decimal
    cost_per_base_cents: Decimal | None
    display_unit: str = "each"


@dataclass(frozen=True)
class Valued:
    holding: Holding
    value_cents: int


@dataclass(frozen=True)
class Valuation:
    total_cents: int
    lines: list[Valued]
    without_cost: list[Holding]  # held, but with no cost: their value is unknown, not zero
    basis: str = BASIS


def value_of(qty_base: Decimal, cost_per_base_cents: Decimal | None) -> int:
    """max(recorded, 0) × cost, in whole cents (AC-ANL-02)."""
    if cost_per_base_cents is None:
        return 0
    held = max(Decimal(qty_base), ZERO)
    return int((held * Decimal(cost_per_base_cents)).quantize(Decimal("1")))


def valuation(holdings: Iterable[Holding]) -> Valuation:
    """The value of what is held, item by item, and the items held without a cost."""
    lines = []
    without = []
    for holding in holdings:
        if holding.qty_base > 0 and not holding.cost_per_base_cents:
            without.append(holding)
        lines.append(Valued(holding, value_of(holding.qty_base, holding.cost_per_base_cents)))
    lines.sort(key=lambda v: (-v.value_cents, v.holding.name))
    return Valuation(sum(v.value_cents for v in lines), lines, without)


def cost_at(history: Sequence[tuple[datetime, Decimal]], at: datetime) -> Decimal | None:
    """The cost in effect at an instant: the latest entry at or before it; None before any."""
    found = None
    for effective_at, cost in sorted(history, key=lambda entry: entry[0]):
        if effective_at > at:
            break
        found = cost
    return found
