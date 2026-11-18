"""Batch allocation rules (T2.06; BAT-FR-05..08, DR-13..15, ADR-0056). Pure.

A batch's remaining quantity is the sum of the movements that name it (DR-15). When stock of an
item leaves without a person choosing the batch (a sale, a count that found less), the ledger
takes it from the item's batches in order: soonest expiry first (FEFO) for an item that keeps
dates, earliest received first (FIFO) otherwise, each batch up to what it holds. What no batch
holds leaves as undated stock: a shortfall (DR-13).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

FEFO, FIFO = "fefo", "fifo"
BATCH_ORDERS = (FEFO, FIFO)
ORDER_LABELS = {
    FEFO: "Soonest expiry first",
    FIFO: "First received first",
}
ACTIVE, DEPLETED, EXPIRED = "active", "depleted", "expired"
ZERO = Decimal(0)


class BatchRuleError(ValueError):
    """A batch change the rules refuse; nothing is written."""


@dataclass(frozen=True)
class Held:
    """What one batch holds now, and what orders it."""

    batch_id: int
    remaining: Decimal
    expires_on: date
    received_at: datetime


def in_order(held: Iterable[Held], rule: str) -> list[Held]:
    """The batches in the order stock leaves them (BAT-FR-05); ties go to the older batch."""
    if rule not in BATCH_ORDERS:
        raise BatchRuleError(f"{rule!r} is not a batch order")
    if rule == FIFO:
        return sorted(held, key=lambda h: (h.received_at, h.batch_id))
    return sorted(held, key=lambda h: (h.expires_on, h.received_at, h.batch_id))


def take(
    amount: Decimal, held: Iterable[Held], rule: str
) -> tuple[list[tuple[int, Decimal]], Decimal]:
    """Take `amount` (above zero) from the batches in order, each up to what it holds: the
    (batch, quantity) taken, and the shortfall no batch held."""
    if amount <= ZERO:
        raise BatchRuleError("only a quantity above zero is taken")
    left = amount
    taken: list[tuple[int, Decimal]] = []
    for batch in in_order(held, rule):
        if left <= ZERO:
            break
        if batch.remaining <= ZERO:
            continue
        part = min(batch.remaining, left)
        taken.append((batch.batch_id, part))
        left -= part
    return taken, max(left, ZERO)


def undated(balance: Decimal, held: Iterable[Held]) -> Decimal:
    """Stock that no batch holds: the balance less what the batches hold, never below zero."""
    return max(Decimal(balance) - sum((h.remaining for h in held), ZERO), ZERO)


def status(remaining: Decimal, expires_on: date, today: date) -> str:
    """A batch is depleted when it holds nothing, expired when it still holds stock past its
    date (BAT-FR-07), active otherwise. An expired batch stays until something accounts for
    what it holds (DR-14)."""
    if remaining <= ZERO:
        return DEPLETED
    if expires_on < today:
        return EXPIRED
    return ACTIVE
