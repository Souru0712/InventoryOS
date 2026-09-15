"""Pure allocation math for paid snack boxes: what the till may still sell, and who goes short.

Square has no reserved state, so a paid box's units are sold out of IN_STOCK as soon as the box
is allocated. Until that change lands the units are still inside the till's count, so every line
records whether its units have been taken out of the count yet ("applied").
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

ZERO = Decimal("0")

# Cancelled is for orders never paid; once money has moved the only way out is a refund.
ORDER_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending_payment": frozenset({"paid", "cancelled"}),
    "paid": frozenset({"packed", "refunded"}),
    "packed": frozenset({"picked_up", "refunded"}),
    "picked_up": frozenset({"refunded"}),
    "cancelled": frozenset(),
    "refunded": frozenset(),
}


class TransitionError(ValueError):
    """An order was asked to move somewhere its current state does not allow."""


def check_transition(current: str, target: str) -> None:
    if target not in ORDER_TRANSITIONS.get(current, frozenset()):
        raise TransitionError(
            f"a box order that is {current.replace('_', ' ')} cannot become {target.replace('_', ' ')}"
        )


@dataclass(frozen=True)
class Allocation:
    """One live box line for an item: paid for and not released."""

    line_id: int
    order_id: int
    paid_at: datetime
    qty_base: Decimal
    applied: bool  # already taken out of the count the till sells from
    packed: bool


@dataclass(frozen=True)
class Position:
    """One item's stock as the till, the paid boxes and the shelf each see it."""

    count_base: Decimal
    allocations: tuple[Allocation, ...]

    def _sum(self, keep: Callable[[Allocation], bool]) -> Decimal:
        return sum((a.qty_base for a in self.allocations if keep(a)), ZERO)

    @property
    def allocated_base(self) -> Decimal:
        return self._sum(lambda a: not a.packed)

    @property
    def packed_base(self) -> Decimal:
        return self._sum(lambda a: a.packed)

    @property
    def unapplied_base(self) -> Decimal:
        return self._sum(lambda a: not a.applied)

    @property
    def store_available_base(self) -> Decimal:
        """What walk-in customers may still buy: the count, less box units it was never told about."""
        return self.count_base - self.unapplied_base

    @property
    def on_shelf_base(self) -> Decimal:
        """Physical units. Applied but unpacked units are still on the shelf; packed unapplied ones are not."""
        return (
            self.count_base
            + self._sum(lambda a: a.applied and not a.packed)
            - self._sum(lambda a: a.packed and not a.applied)
        )


def position(count_base: Decimal, allocations: list[Allocation]) -> Position:
    return Position(Decimal(count_base), tuple(allocations))


def short_lines(pos: Position) -> list[int]:
    """Unpacked lines whose units the till has sold, newest paid first so earlier customers keep theirs."""
    missing = -pos.store_available_base
    shorted: list[int] = []
    unpacked = sorted(
        (a for a in pos.allocations if not a.packed),
        key=lambda a: (a.paid_at, a.line_id),
        reverse=True,
    )
    for allocation in unpacked:
        if missing <= ZERO:
            break
        shorted.append(allocation.line_id)
        missing -= allocation.qty_base
    return shorted
