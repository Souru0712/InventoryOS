"""Recipe rules (T2.02; RCP-FR-02, -04..09, -14, DR-11, DR-16, DR-17). Pure.

A recipe belongs to one owner: a product variation, an "add" modifier, or a prepared item (its
production recipe). Every save is a new immutable version effective from a moment; a sale, a
waste expansion or a production run uses the version effective at its own time, so editing a
recipe never changes the past (DR-16). Prepared items may use prepared items, but never in a
circle (DR-17).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

OWNER_KINDS = ("variation", "modifier", "item")
RECIPE_ITEM_TYPES = ("prepared", "finished")  # items made in house have production recipes
SERVING = "serving"
PLACES = Decimal("0.0001")
# A save "now" may arrive a moment late; anything earlier would rewrite the past (DR-16).
CLOCK_SKEW = timedelta(minutes=1)


class RecipeRuleError(ValueError):
    """A recipe the rules refuse; nothing is written."""


@dataclass(frozen=True)
class Line:
    """One ingredient line: what was entered, and the same in the item's base unit."""

    inventory_item_id: int
    quantity: Decimal
    unit: str
    qty_base: Decimal
    is_packaging: bool = False


def check_quantity(raw: str, what: str = "A quantity") -> Decimal:
    try:
        amount = Decimal((raw or "").strip())
    except InvalidOperation as exc:
        raise RecipeRuleError(f"{what} is a number like 220 or 1.5.") from exc
    if not amount.is_finite() or amount <= 0:
        raise RecipeRuleError(f"{what} must be above zero.")
    return amount.quantize(PLACES)


def check_lines(lines: Sequence[Line], names: Mapping[int, str]) -> list[Line]:
    """The lines a version keeps: at least one, each item once, every quantity above zero."""
    if not lines:
        raise RecipeRuleError("A recipe needs at least one ingredient.")
    seen: set[int] = set()
    for line in lines:
        if line.inventory_item_id in seen:
            name = names.get(line.inventory_item_id, "An item")
            raise RecipeRuleError(f"{name} is listed twice; put it on one line.")
        seen.add(line.inventory_item_id)
        if line.quantity <= 0 or line.qty_base <= 0:
            raise RecipeRuleError("Every quantity must be above zero.")
    return list(lines)


def check_effective_from(at: datetime | None, now: datetime) -> datetime:
    """When a new version takes effect: now, or a chosen later moment (RCP-FR-07)."""
    if at is None:
        return now
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("an instant needs a time zone")
    if at < now - CLOCK_SKEW:
        raise RecipeRuleError("A new version starts now or later; the past keeps its recipe.")
    return at


def find_cycle(owner: int, uses: Mapping[int, Iterable[int]], lines: Iterable[int]) -> list[int]:
    """A path from the owner back to itself through prepared items, or [] when there is none
    (DR-17). `uses` maps each item with a recipe to the items its current version uses."""
    for start in lines:
        path = _reach(start, owner, uses, [owner, start], set())
        if path:
            return path
    return []


def _reach(
    node: int, target: int, uses: Mapping[int, Iterable[int]], path: list[int], seen: set[int]
) -> list[int]:
    if node == target:
        return path
    if node in seen:
        return []
    seen.add(node)
    for nxt in uses.get(node, ()):
        found = _reach(nxt, target, uses, [*path, nxt], seen)
        if found:
            return found
    return []


def version_at(versions: Iterable[tuple[int, datetime]], at: datetime) -> int | None:
    """The version effective at an instant: the latest one that had started by then, the
    higher number winning a tie; None before the first (DR-16, AC-RCP-02)."""
    started = [(effective, number) for number, effective in versions if effective <= at]
    return max(started)[1] if started else None


_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-z ]*?)\s*$", re.IGNORECASE)


def size_ratio(from_name: str, to_name: str) -> Decimal:
    """How much bigger one size is than another when both name an amount in the same unit
    ("12 oz" to "16 oz" is 16/12); 1 when the names say nothing comparable (RCP-FR-09)."""
    a, b = _SIZE.match(from_name or ""), _SIZE.match(to_name or "")
    if not a or not b or a.group(2).strip().lower() != b.group(2).strip().lower():
        return Decimal(1)
    low, high = Decimal(a.group(1)), Decimal(b.group(1))
    return high / low if low > 0 else Decimal(1)


def scale_lines(lines: Iterable[Line], factor: Decimal) -> list[Line]:
    """Lines scaled by a factor, kept to four places, for "start from" (AC-RCP-06)."""
    return [
        replace(
            line,
            quantity=(line.quantity * factor).quantize(PLACES),
            qty_base=(line.qty_base * factor).quantize(PLACES),
        )
        for line in lines
    ]


def same_recipe(
    a: Sequence[Line],
    b: Sequence[Line],
    a_extra: tuple[Decimal, str, str],
    b_extra: tuple[Decimal, str, str],
) -> bool:
    """Whether a save changes nothing (lines in any order, yield and instructions)."""

    def key(lines: Sequence[Line]) -> list[tuple[int, Decimal, str, bool]]:
        return sorted(
            (ln.inventory_item_id, ln.quantity.quantize(PLACES), ln.unit, ln.is_packaging)
            for ln in lines
        )

    return key(a) == key(b) and a_extra == b_extra
