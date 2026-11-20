"""Reorder suggestions (T2.15; ROR-FR-01..05, ROR-FR-08, STD-FR-09, WF-21). Pure.

Average daily use comes from the ledger: what sales, production and usage records took over the
window. Where a location's sales are incomplete, or nothing recorded took the item, the use worked
out between two counts takes its place (AC-ANL-08), and the suggestion says which it used.
Suggested quantity = max(0, use per day x (lead time + cover days) x (1 + safety %) + reorder
point - on hand - on order), rounded up to the purchase unit. Negative, never-counted or stale
stock is "count first" instead. Nothing here orders anything (ROR-FR-08).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

ZERO = Decimal(0)
USAGE_TYPES = frozenset({"sale_consumption", "production_input", "manual_consumption"})
# Not explained movements between two counts: usage itself and what counts and dating moved.
_UNEXPLAINED = USAGE_TYPES | {"count_variance", "batch_assignment"}
# A count of one batch observes only that batch, not the item's whole quantity.
PART_COUNTS = frozenset({"batch"})
STALE_DAYS = 14
WINDOWS = (7, 14, 28, 56, 90)
DEFAULT_WINDOW = 28
DEFAULT_COVER_DAYS = 7
DEFAULT_SAFETY_PERCENT = Decimal(10)


class ReorderRuleError(ValueError):
    """Settings for the suggestions that cannot be used, with the reason to show."""


def check_settings(window: int, cover_days: int, safety_percent: Decimal) -> None:
    if window not in WINDOWS:
        raise ReorderRuleError(f"Average over {', '.join(map(str, WINDOWS))} days.")
    if not 0 <= cover_days <= 60:
        raise ReorderRuleError("Cover between 0 and 60 days.")
    if not ZERO <= safety_percent <= Decimal(100):
        raise ReorderRuleError("Safety between 0 and 100 %.")


@dataclass(frozen=True)
class Move:
    """One ledger movement of the item at its location; a reversal has the type it undid."""

    at: datetime
    day: date
    transaction_id: int
    kind: str
    source_type: str
    movement_type: str
    qty_base: Decimal


@dataclass(frozen=True)
class Observation:
    """What a count found the item's whole quantity to be."""

    at: datetime
    day: date
    qty_base: Decimal


def recorded_use(moves: Iterable[Move]) -> Decimal:
    """What sales, production and usage records took, net of their reversals."""
    return -sum((m.qty_base for m in moves if m.movement_type in USAGE_TYPES), ZERO)


def observations(
    opening: Decimal, moves: Sequence[Move], counted: Iterable[Observation]
) -> list[Observation]:
    """Every count of the item, oldest first: the balance after each count posting, and every
    count-session line (which also keeps a count that matched the record and posted nothing)."""
    found = {o.at: o for o in counted}
    running = opening
    for index, move in enumerate(moves):
        running += move.qty_base
        last = index + 1 == len(moves) or moves[index + 1].transaction_id != move.transaction_id
        if last and move.kind == "count" and move.source_type not in PART_COUNTS:
            found.setdefault(move.at, Observation(move.at, move.day, running))
    return [found[at] for at in sorted(found)]


@dataclass(frozen=True)
class CountUse:
    """Use between two counts: first count + what else came and went - last count (AC-ANL-08)."""

    first: Observation
    last: Observation
    explained: Decimal  # receipts, production, waste, adjustments between the two
    used: Decimal

    @property
    def days(self) -> int:
        return (self.last.day - self.first.day).days

    @property
    def per_day(self) -> Decimal:
        return (self.used / Decimal(self.days)).quantize(Decimal("0.0001"))


def count_based_use(moves: Iterable[Move], counts: Sequence[Observation]) -> CountUse | None:
    """Use between the first and last count of the window; None without two counts a day or
    more apart. Stock that grew unexplained is no use, not negative use."""
    if len(counts) < 2:
        return None
    first, last = counts[0], counts[-1]
    if (last.day - first.day).days < 1:
        return None
    explained = sum(
        (
            m.qty_base
            for m in moves
            if first.at < m.at <= last.at and m.movement_type not in _UNEXPLAINED
        ),
        ZERO,
    )
    used = max(first.qty_base + explained - last.qty_base, ZERO)
    return CountUse(first, last, explained, used)


@dataclass(frozen=True)
class Usage:
    """Average daily use and what it rests on (ROR-FR-01, STD-FR-09)."""

    per_day: Decimal
    basis: str  # recorded | counts | none
    window_days: int
    recorded: Decimal
    sales_complete: bool
    counts: CountUse | None = None

    @property
    def note(self) -> str:
        days = f"{self.window_days} days"
        if self.basis == "counts" and self.counts is not None:
            span = f"counts on {self.counts.first.day:%d %b} and {self.counts.last.day:%d %b}"
            why = (
                "sales are incomplete, so recorded use would be too low"
                if not self.sales_complete
                else "nothing recorded used it"
            )
            return f"From {span}: {why}."
        if self.basis == "recorded" and self.sales_complete:
            return f"Recorded sales, production and usage over {days}."
        if self.basis == "recorded":
            return (
                f"Recorded use over {days}, but sales are incomplete, so this may be low. "
                "Two counts would give use from counts."
            )
        return f"No use recorded or counted over {days}."


def daily_use(
    recorded: Decimal, window_days: int, sales_complete: bool, counts: CountUse | None
) -> Usage:
    """Recorded use while sales are complete; otherwise use from counts where there are two;
    otherwise what was recorded, saying it may be low."""
    if recorded > ZERO and sales_complete:
        per_day = (recorded / Decimal(window_days)).quantize(Decimal("0.0001"))
        return Usage(per_day, "recorded", window_days, recorded, True, counts)
    if counts is not None:
        return Usage(counts.per_day, "counts", window_days, recorded, sales_complete, counts)
    if recorded > ZERO:
        per_day = (recorded / Decimal(window_days)).quantize(Decimal("0.0001"))
        return Usage(per_day, "recorded", window_days, recorded, sales_complete)
    return Usage(ZERO, "none", window_days, recorded, sales_complete)


def count_first(on_hand: Decimal, last_counted_at: datetime | None, now: datetime) -> str | None:
    """Why the recorded quantity cannot be trusted for an order, or None (ROR-FR-05, AC-ROR-03)."""
    if on_hand < ZERO:
        return "Recorded stock is below zero: count it first."
    if last_counted_at is None:
        return "Never counted: count it first."
    days = (now - last_counted_at).days
    if days > STALE_DAYS:
        return f"Last counted {days} days ago: count it first."
    return None


@dataclass(frozen=True)
class Inputs:
    """Everything the suggested quantity is worked out from (ROR-FR-03)."""

    per_day: Decimal
    lead_days: int
    cover_days: int
    safety_percent: Decimal
    reorder_point: Decimal
    on_hand: Decimal
    on_order: Decimal = ZERO
    pack: Decimal | None = None  # the purchase unit in base units
    whole_units: bool = False  # counted items order whole units when no pack is known

    @property
    def factor(self) -> Decimal:
        return Decimal(1) + self.safety_percent / Decimal(100)


@dataclass(frozen=True)
class Suggestion:
    raw: Decimal  # the formula before rounding; at or below zero orders nothing
    quantity: Decimal
    packs: int | None  # purchase units, where the pack is known


def suggest(inputs: Inputs) -> Suggestion:
    """ROR-FR-02: max(0, use x (lead + cover) x (1 + safety) + reorder point - on hand - on
    order), rounded up to the purchase unit."""
    raw = (
        inputs.per_day * Decimal(inputs.lead_days + inputs.cover_days) * inputs.factor
        + inputs.reorder_point
        - inputs.on_hand
        - inputs.on_order
    ).quantize(Decimal("0.0001"))
    need = max(raw, ZERO)
    pack = inputs.pack if inputs.pack is not None and inputs.pack > ZERO else None
    if pack is not None:
        packs = int((need / pack).to_integral_value(rounding=ROUND_CEILING))
        return Suggestion(raw, pack * packs, packs)
    step = Decimal(1) if inputs.whole_units else Decimal("0.01")
    return Suggestion(raw, need.quantize(step, rounding=ROUND_CEILING), None)


def needs_order(inputs: Inputs) -> bool:
    """At or below the reorder point, or projected to reach it within lead time + cover (WF-21)."""
    if inputs.on_hand <= inputs.reorder_point:
        return True
    horizon = Decimal(inputs.lead_days + inputs.cover_days)
    return (
        inputs.per_day > ZERO and inputs.on_hand - inputs.per_day * horizon <= inputs.reorder_point
    )


def order_by(today: date, inputs: Inputs, delivery_days: int = 0) -> date:
    """The last day to order so a delivery lands before stock reaches the reorder point, moved
    earlier to arrive on one of the supplier's delivery days (Monday is bit 0); never before
    today (ROR-FR-04)."""
    if inputs.per_day <= ZERO or inputs.on_hand <= inputs.reorder_point:
        latest = today
    else:
        left = ((inputs.on_hand - inputs.reorder_point) / inputs.per_day).to_integral_value(
            rounding=ROUND_FLOOR
        )
        latest = today + timedelta(days=int(left) - inputs.lead_days)
    if delivery_days:
        for _ in range(7):
            if delivery_days >> (latest + timedelta(days=inputs.lead_days)).weekday() & 1:
                break
            latest -= timedelta(days=1)
    return max(latest, today)


@dataclass(frozen=True)
class Line:
    stock_item_id: int
    name: str
    location_name: str
    unit: str
    supplier: str  # empty when none is set
    purchase_unit: str | None
    usage: Usage
    inputs: Inputs
    order_by: date
    count_first: str | None = None
    suggestion: Suggestion | None = None  # None while it must be counted first


@dataclass(frozen=True)
class Group:
    supplier: str
    order_by: date
    lines: tuple[Line, ...]


def group(lines: Iterable[Line]) -> list[Group]:
    """By supplier, the most urgent order first; stock with no supplier last, where it is easy to
    see. Within a supplier, the soonest order-by first."""
    by_supplier: dict[str, list[Line]] = {}
    for line in lines:
        by_supplier.setdefault(line.supplier, []).append(line)
    groups = [
        Group(
            name,
            min(line.order_by for line in found),
            tuple(sorted(found, key=lambda line: (line.order_by, line.name.lower()))),
        )
        for name, found in by_supplier.items()
    ]
    return sorted(groups, key=lambda g: (g.supplier == "", g.order_by, g.supplier.lower()))
