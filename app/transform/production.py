"""Production rules (T2.09; PRD-FR-02..08, -12, PD-15, AC-PRD-01..07, ADR-0060). Pure.

A run makes a prepared item (or a finished good) from its production recipe in effect, times a
multiplier: inputs leave stock, the output arrives as a new batch with a use-by date and a
readable code. Its unit cost is what the inputs cost over what was actually made, or unknown if
any input has no cost, never understated (PRD-FR-06).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

STATUSES = ("completed", "voided")
VOID_WINDOW = timedelta(minutes=30)
ZERO = Decimal(0)
PLACES = Decimal("0.0001")


class ProductionRuleError(ValueError):
    """A run or void the rules refuse; nothing is written."""


@dataclass(frozen=True)
class Planned:
    """What a run takes and what it should make (PRD-FR-03)."""

    inputs: dict[int, Decimal]  # item id -> base quantity
    expected_yield: Decimal


def plan(lines: Iterable[tuple[int, Decimal]], yield_base: Decimal, multiplier: Decimal) -> Planned:
    """The recipe's lines and yield, times the multiplier."""
    if multiplier <= ZERO:
        raise ProductionRuleError("The multiplier is above zero.")
    if yield_base <= ZERO:
        raise ProductionRuleError("The recipe makes nothing: give it a yield.")
    inputs: dict[int, Decimal] = defaultdict(Decimal)
    for item_id, qty in lines:
        inputs[item_id] += Decimal(qty) * multiplier
    if not inputs:
        raise ProductionRuleError("The recipe has no ingredients.")
    return Planned(
        {item: qty.quantize(PLACES) for item, qty in inputs.items()},
        (Decimal(yield_base) * multiplier).quantize(PLACES),
    )


def check_yield(actual: Decimal) -> Decimal:
    if actual <= ZERO:
        raise ProductionRuleError("Enter what was made, above zero.")
    return actual.quantize(PLACES)


def output_cost(
    inputs: Iterable[tuple[Decimal, Decimal | None]], actual: Decimal
) -> Decimal | None:
    """Cost per base unit of the output: Σ quantity × cost of the inputs over the actual yield;
    None (unknown) when any input has no cost (PRD-FR-06, AC-PRD-02, -03)."""
    total = ZERO
    for qty, cost in inputs:
        if not cost:
            return None
        total += Decimal(qty) * Decimal(cost)
    return (total / actual).quantize(PLACES)


def use_by(day: date, shelf_life_days: int | None) -> date:
    """Production day plus the item's shelf life (PRD-FR-05); the same day when it has none."""
    return day + timedelta(days=shelf_life_days or 0)


def batch_code(name: str, day: date, number: int) -> str:
    """A code people can read off a label: initials, month and day, the run's number that day
    (PRD-FR-12), e.g. GJ-1105-1 for the first ginger juice on 5 November."""
    words = [w for w in name.replace("-", " ").split() if w[:1].isalnum()]
    initials = "".join(w[0] for w in words[:3]).upper() or "B"
    return f"{initials}-{day:%m%d}-{number}"


def void_refusal(
    *,
    recorded_by: int | None,
    recorded_at: datetime,
    person: int | None,
    may_void_any: bool,
    reason: str,
    now: datetime,
) -> str | None:
    """Why a void is refused, or None (PRD-FR-07, AC-PRD-05)."""
    if person is not None and person == recorded_by and now - recorded_at <= VOID_WINDOW:
        return None
    if not may_void_any:
        return "After 30 minutes, or for someone else's run, a Manager voids it."
    if not reason.strip():
        return "Say why it is being voided."
    return None
