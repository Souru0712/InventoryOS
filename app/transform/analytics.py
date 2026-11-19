"""Operational reports (T2.16; ANL-FR-03, -05, -07, AC-ANL-03). Pure.

Every figure is read from ledger movements over each location's business days: quantities in
the item's base unit, money at each movement's own cost snapshot (ANL-FR-01), so a later cost
change never moves a past figure (AC-ANL-01). A reversal arrives with the type it undid and
nets against it. Values are worked out per movement and rounded to whole cents there, exactly as
the ledger's own totals are, so the report's totals match them (AC-ANL-03).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.transform.dashboard import movement_value_cents

ZERO = Decimal(0)
# What took stock out, by where it went (ANL-FR-03): the four parts of consumption.
CONSUMPTION = {
    "sale_consumption": "sales",
    "production_input": "production",
    "waste": "waste",
    "manual_consumption": "manual",
}
PARTS = ("sales", "production", "waste", "manual")


@dataclass(frozen=True)
class Moved:
    """One movement as a report reads it."""

    location_id: int
    item_id: int
    movement_type: str  # a reversal carries the type it undid
    qty_base: Decimal
    unit_cost: Decimal  # cents per base unit, as posted
    expired: bool = False  # waste logged for stock past its date


@dataclass
class Split:
    """What one item at one location used over the period, by part: positive base units and
    whole cents."""

    qty: dict[str, Decimal] = field(default_factory=lambda: dict.fromkeys(PARTS, ZERO))
    cents: dict[str, int] = field(default_factory=lambda: dict.fromkeys(PARTS, 0))
    uncosted: bool = False

    @property
    def total_qty(self) -> Decimal:
        return sum(self.qty.values(), ZERO)

    @property
    def total_cents(self) -> int:
        return sum(self.cents.values())


@dataclass(frozen=True)
class ConsumptionReport:
    rows: dict[tuple[int, int], Split]  # (location, item)
    totals_cents: dict[str, int]
    uncosted: bool

    @property
    def total_cents(self) -> int:
        return sum(self.totals_cents.values())


def consumption(moves: Iterable[Moved]) -> ConsumptionReport:
    """Ingredient use by item, split into sales, production, waste and manual usage
    (ANL-FR-03). An item whose movements all netted out is left out."""
    rows: dict[tuple[int, int], Split] = {}
    totals = dict.fromkeys(PARTS, 0)
    uncosted = False
    for m in moves:
        part = CONSUMPTION.get(m.movement_type)
        if part is None:
            continue
        row = rows.setdefault((m.location_id, m.item_id), Split())
        cents = -movement_value_cents(m.qty_base, m.unit_cost)
        row.qty[part] -= m.qty_base
        row.cents[part] += cents
        totals[part] += cents
        if m.unit_cost == ZERO:
            row.uncosted = uncosted = True
    kept = {key: row for key, row in rows.items() if any(row.qty.values())}
    return ConsumptionReport(kept, totals, uncosted)


@dataclass
class Variance:
    """What counts found against the record for one item at one location (ANL-FR-07): signed
    base units (negative is short) and its value at the count's snapshot."""

    qty: Decimal = ZERO
    cents: int = 0
    short_cents: int = 0  # negative or zero
    over_cents: int = 0  # positive or zero
    uncosted: bool = False


@dataclass(frozen=True)
class VarianceReport:
    rows: dict[tuple[int, int], Variance]
    short_cents: int
    over_cents: int
    uncosted: bool

    @property
    def net_cents(self) -> int:
        return self.short_cents + self.over_cents


def count_variance(moves: Iterable[Moved]) -> VarianceReport:
    """Count variances by item, quantity and value; short and over kept apart."""
    rows: dict[tuple[int, int], Variance] = {}
    for m in moves:
        if m.movement_type != "count_variance":
            continue
        row = rows.setdefault((m.location_id, m.item_id), Variance())
        cents = movement_value_cents(m.qty_base, m.unit_cost)
        row.qty += m.qty_base
        row.cents += cents
        if cents < 0:
            row.short_cents += cents
        else:
            row.over_cents += cents
        if m.unit_cost == ZERO:
            row.uncosted = True
    kept = {key: row for key, row in rows.items() if row.qty or row.cents}
    return VarianceReport(
        kept,
        sum(r.short_cents for r in kept.values()),
        sum(r.over_cents for r in kept.values()),
        any(r.uncosted for r in kept.values()),
    )


@dataclass(frozen=True)
class AtRisk:
    """Stock past its date still on the shelf: an estimate of loss to come (ANL-FR-05)."""

    location_id: int
    item_id: int
    batch_id: int
    expires_on: date
    remaining: Decimal
    cents: int | None  # None without a cost


@dataclass(frozen=True)
class ExpiryReport:
    lost: dict[tuple[int, int], tuple[Decimal, int]]  # (location, item) -> (base units, cents)
    lost_cents: int
    at_risk: tuple[AtRisk, ...]
    uncosted: bool

    @property
    def at_risk_cents(self) -> int:
        return sum(r.cents or 0 for r in self.at_risk)


def expiry_losses(moves: Iterable[Moved], at_risk: Iterable[AtRisk]) -> ExpiryReport:
    """Waste logged as past its date, valued at its snapshots, and expired stock not yet dealt
    with (ANL-FR-05)."""
    lost: dict[tuple[int, int], tuple[Decimal, int]] = {}
    uncosted = False
    for m in moves:
        if m.movement_type != "waste" or not m.expired:
            continue
        qty, cents = lost.get((m.location_id, m.item_id), (ZERO, 0))
        lost[(m.location_id, m.item_id)] = (
            qty - m.qty_base,
            cents - movement_value_cents(m.qty_base, m.unit_cost),
        )
        uncosted = uncosted or m.unit_cost == ZERO
    kept = {key: value for key, value in lost.items() if value[0]}
    risky = tuple(sorted(at_risk, key=lambda r: (r.expires_on, r.batch_id)))
    return ExpiryReport(
        kept,
        sum(cents for _, cents in kept.values()),
        risky,
        uncosted or any(r.cents is None for r in risky),
    )
