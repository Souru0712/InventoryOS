"""Operational reports (T2.16; ANL-FR-03, -05, -07, AC-ANL-03). Pure.

Every figure is read from ledger movements over each location's business days: quantities in
the item's base unit, money at each movement's own cost snapshot (ANL-FR-01), so a later cost
change never moves a past figure (AC-ANL-01). A reversal arrives with the type it undid and
nets against it. Values are worked out per movement and rounded to whole cents there, exactly as
the ledger's own totals are, so the report's totals match them (AC-ANL-03).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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


# --- count-based actual usage (ANL-FR-14, STD-FR-08, AC-ANL-08) -------------------------------

# What a count interval explains, by line of the formula; anything else that moved stock between
# two counts (sales, manual usage, losses nobody recorded) is what was used.
EXPLAINED = {
    "receipt": "received",
    "production_output": "produced",
    "production_input": "used_in_production",
    "waste": "wasted",
    "adjustment": "adjusted",
    "opening_balance": "adjusted",
    "transfer_in": "adjusted",
    "transfer_out": "adjusted",
}


@dataclass(frozen=True)
class Count:
    """A count of the item's whole quantity."""

    at: datetime
    day: date
    qty_base: Decimal


@dataclass(frozen=True)
class Timed:
    """A movement with its instant, for placing it between two counts."""

    at: datetime
    movement_type: str
    qty_base: Decimal


@dataclass(frozen=True)
class Interval:
    """Actual usage between two consecutive counts: opening count + received + produced
    - used in production - wasted +/- adjusted - closing count."""

    opening: Count
    closing: Count
    parts: dict[str, Decimal]  # signed, as each moved stock

    @property
    def used(self) -> Decimal:
        return self.opening.qty_base + sum(self.parts.values(), ZERO) - self.closing.qty_base

    @property
    def days(self) -> int:
        return (self.closing.day - self.opening.day).days


def count_intervals(moves: Iterable[Timed], counts: Iterable[Count]) -> list[Interval]:
    """Usage per interval between consecutive counts (counts at the same instant are one).
    It needs no sales data at all."""
    ordered = sorted({c.at: c for c in counts}.values(), key=lambda c: c.at)
    timed = sorted(moves, key=lambda m: m.at)
    result = []
    for opening, closing in zip(ordered, ordered[1:], strict=False):
        parts = dict.fromkeys(dict.fromkeys(EXPLAINED.values()), ZERO)
        for m in timed:
            part = EXPLAINED.get(m.movement_type)
            if part is not None and opening.at < m.at <= closing.at:
                parts[part] += m.qty_base
        result.append(Interval(opening, closing, parts))
    return result


# --- receiving: price changes (ANL-FR-08) -------------------------------------------------------


@dataclass(frozen=True)
class Price:
    """What one posted receiving line paid per base unit of an item from a supplier."""

    key: tuple[int, int]  # (supplier, item)
    at: datetime
    per_base_cents: Decimal
    receiving_id: int


@dataclass(frozen=True)
class PriceChange:
    key: tuple[int, int]
    at: datetime
    before_cents: Decimal
    after_cents: Decimal
    receiving_id: int

    @property
    def percent(self) -> Decimal:
        return ((self.after_cents - self.before_cents) * 100 / self.before_cents).quantize(
            Decimal("0.1")
        )


def price_changes(prices: Iterable[Price], since: datetime) -> list[PriceChange]:
    """Each change from the previous price of the same item from the same supplier, for prices
    paid from `since` on: (new - previous) / previous."""
    by_key: dict[tuple[int, int], list[Price]] = {}
    for price in sorted(prices, key=lambda p: (p.at, p.receiving_id)):
        by_key.setdefault(price.key, []).append(price)
    changes = []
    for paid in by_key.values():
        for before, after in zip(paid, paid[1:], strict=False):
            if (
                after.at >= since
                and before.per_base_cents > ZERO
                and (after.per_base_cents != before.per_base_cents)
            ):
                changes.append(
                    PriceChange(
                        after.key,
                        after.at,
                        before.per_base_cents,
                        after.per_base_cents,
                        after.receiving_id,
                    )
                )
    return sorted(changes, key=lambda c: c.at)


# --- realized ingredient cost of sales (ANL-FR-12, AC-ANL-04) -----------------------------------


@dataclass(frozen=True)
class CostOfSales:
    """What sales took, at each movement's snapshot, and how much of what was sold had a recipe
    that took it."""

    cents: int
    lines: int  # counted sale lines that should take stock
    complete: int  # of those, the ones fully consumed through a recipe
    uncosted: bool

    @property
    def coverage_percent(self) -> Decimal | None:
        if not self.lines:
            return None
        return (Decimal(self.complete) * 100 / Decimal(self.lines)).quantize(Decimal("0.1"))


# --- where sales came from, and Standalone coverage (STD-FR-07, AC-SAL-08) ----------------------

# Sources whose days are Standalone: sales someone records, so a day can be missing.
STANDALONE_ORIGINS = frozenset({"manual", "file_import"})


@dataclass(frozen=True)
class SalesCoverage:
    """Where a location's counted sales over a period came from, and how many of its Standalone
    open days have any."""

    location_id: int
    days_by_origin: dict[str, int]  # business days with counted sales, by origin
    with_sales: int  # Standalone open days with counted sales
    open_days: int  # Standalone open days in the period

    @property
    def standalone(self) -> bool:
        return self.open_days > 0

    @property
    def missing(self) -> int:
        return self.open_days - self.with_sales


def sales_coverage(
    location_id: int,
    first: date,
    last: date,
    today: date,
    open_mask: int,
    record: Mapping[date, str],
    sold: Mapping[date, Iterable[str]],
) -> SalesCoverage:
    """`record` is each day's source-of-record origin, `sold` the origins of its counted sales.
    A Standalone open day counts once it is over, or sooner once it has sales, so an unfinished
    today never shows as missing; days after today never count."""
    days_by_origin: dict[str, int] = {}
    with_sales = open_days = 0
    day = first
    while day <= min(last, today):
        origins = set(sold.get(day, ()))
        for origin in origins:
            days_by_origin[origin] = days_by_origin.get(origin, 0) + 1
        is_open = open_mask >> day.weekday() & 1
        if is_open and record.get(day) in STANDALONE_ORIGINS and (day < today or origins):
            open_days += 1
            with_sales += bool(origins)
        day += timedelta(days=1)
    return SalesCoverage(location_id, dict(sorted(days_by_origin.items())), with_sales, open_days)
