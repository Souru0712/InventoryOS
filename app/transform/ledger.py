"""The ledger's rules (T1.16; DR-01..07, INV-FR-15..18): what a posting may contain. Pure.

Every change to stock is a transaction of one kind (a receipt, a count, ...) made of movements,
each a signed quantity of one item at one location in its base unit. A movement is never zero;
its sign follows its type (the database checks it too); a transaction holds only the movement
types its kind allows. Balances are the sum of movements and nothing else.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

# DOMAIN_MODEL §4.2, INV-FR-15.
INCREASES = frozenset({"receipt", "production_output", "transfer_in"})
DECREASES = frozenset(
    {"sale_consumption", "production_input", "waste", "manual_consumption", "transfer_out"}
)
EITHER_WAY = frozenset(
    {"opening_balance", "count_variance", "adjustment", "pos_correction", "reversal"}
)
MOVEMENT_TYPES = INCREASES | DECREASES | EITHER_WAY

# Which movement types each kind of transaction may hold.
TRANSACTION_KINDS: Mapping[str, frozenset[str]] = {
    "opening": frozenset({"opening_balance"}),
    "receipt": frozenset({"receipt"}),
    "sale": frozenset({"sale_consumption"}),
    "production": frozenset({"production_input", "production_output"}),
    "waste": frozenset({"waste"}),
    "count": frozenset({"count_variance"}),
    "adjustment": frozenset({"adjustment"}),
    "usage": frozenset({"manual_consumption"}),
    "transfer": frozenset({"transfer_out", "transfer_in"}),
    "pos_correction": frozenset({"pos_correction"}),
    "reversal": frozenset({"reversal"}),
}

ALLOCATION_MODES = ("explicit", "inferred", "none")
PLACES = Decimal("0.0001")


class LedgerRuleError(ValueError):
    """A posting the ledger refuses; nothing is written."""


@dataclass(frozen=True)
class Line:
    """One movement to post: a signed base-unit quantity of an item at a location."""

    location_id: int
    inventory_item_id: int
    movement_type: str
    qty_base: Decimal
    unit_cost: Decimal | None = None  # cents per base unit; the item's current cost if None
    batch_id: int | None = None
    reason: str | None = None
    reverses_movement_id: int | None = None  # set only by a reversal


def check_line(line: Line) -> None:
    if line.movement_type not in MOVEMENT_TYPES:
        raise LedgerRuleError(f"{line.movement_type!r} is not a movement type")
    if line.qty_base != line.qty_base.quantize(PLACES):
        raise LedgerRuleError("quantities are kept to four decimal places (DR-10)")
    if line.qty_base == 0:
        raise LedgerRuleError("a movement moves something: its quantity cannot be zero (DR-04)")
    if line.movement_type in INCREASES and line.qty_base < 0:
        raise LedgerRuleError(f"a {line.movement_type} adds stock: its quantity is positive")
    if line.movement_type in DECREASES and line.qty_base > 0:
        raise LedgerRuleError(f"a {line.movement_type} removes stock: its quantity is negative")
    if line.unit_cost is not None and line.unit_cost < 0:
        raise LedgerRuleError("a unit cost cannot be negative")


def check_transaction(kind: str, lines: Iterable[Line], idempotency_key: str) -> list[Line]:
    """The lines of a transaction the ledger will accept, or LedgerRuleError."""
    allowed = TRANSACTION_KINDS.get(kind)
    if allowed is None:
        raise LedgerRuleError(f"{kind!r} is not a kind of transaction")
    key = idempotency_key.strip()
    if not key or len(key) > 200:
        raise LedgerRuleError("every posting carries an idempotency key of up to 200 characters")
    accepted = list(lines)
    if not accepted:
        raise LedgerRuleError("a transaction has at least one movement")
    for line in accepted:
        check_line(line)
        if line.movement_type not in allowed:
            raise LedgerRuleError(f"a {kind} transaction cannot hold a {line.movement_type}")
        # Only a reversal undoes something, and it always says what (WF-26, DR-02).
        if (kind == "reversal") != (line.reverses_movement_id is not None):
            raise LedgerRuleError(
                "a reversal names the movement it undoes, and nothing else names one"
            )
    return accepted


def reversal_of(movement_type: str, qty_base: Decimal) -> Decimal:
    """The quantity that undoes a movement (WF-26): the same amount, the other way."""
    if movement_type == "reversal":
        raise LedgerRuleError("a reversal is not reversed again: post an adjustment instead")
    return -qty_base


def apply(
    balances: Mapping[tuple[int, int], Decimal], lines: Iterable[Line]
) -> dict[tuple[int, int], Decimal]:
    """Balances after the lines: each (location, item) moves by the sum of its movements."""
    result = dict(balances)
    for line in lines:
        key = (line.location_id, line.inventory_item_id)
        result[key] = result.get(key, Decimal("0")) + line.qty_base
    return result
