"""Posting to the ledger (T1.16; DR-01..07, DR-19, INV-FR-15..18, -21, -22).

post_transaction() is the single path that writes inventory_transactions, inventory_movements
and stock_balances. A posting is all or nothing: the transaction, its movements and the balances
they move are written together, or nothing is. The balance rows touched are locked in a stable
order (item, then location) before anything is written, so two postings to one item at one
location wait for each other instead of losing an update. Posting the same idempotency key
again returns the first transaction and writes nothing.

It is also the only writer of stock_items.qty_base (T1.18, ADR-0042): that column, which every
screen reads, is kept equal to the balance in the same transaction, as its projection.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.models import (
    InventoryMovement,
    InventoryTransaction,
    ItemLocation,
    StockBalance,
    StockItem,
)
from app.transform.ledger import (
    BelowZero,
    LedgerRuleError,
    Line,
    check_transaction,
    crosses_zero,
    places,
    reversal_of,
)


class _Unset:
    pass


SYSTEM: Final = None  # pass as actor_user_id for a posting the product made itself
_MEMBER: Final = _Unset()


class NotFound(LookupError):
    """A transaction outside the business, or not there at all."""


def _by_key(db: Session, org: OrgContext, key: str) -> InventoryTransaction | None:
    return db.scalars(
        select(InventoryTransaction).where(
            InventoryTransaction.tenant_id == org.tenant_id,
            InventoryTransaction.idempotency_key == key,
        )
    ).first()


def _check_stocked(db: Session, org: OrgContext, pairs: list[tuple[int, int]]) -> None:
    """Every (location, item) is the business's, within org's locations, and stocked there."""
    for location_id, item_id in pairs:
        stocked = db.scalars(
            select_scoped(org, ItemLocation).where(
                ItemLocation.location_id == location_id,
                ItemLocation.inventory_item_id == item_id,
            )
        ).first()
        if stocked is None:
            raise LedgerRuleError("that item is not stocked at that location")


def _locked_balance(db: Session, org: OrgContext, location_id: int, item_id: int) -> StockBalance:
    """The balance row, locked until the posting commits; created at zero the first time."""
    query = (
        select_scoped(org, StockBalance)
        .where(StockBalance.location_id == location_id, StockBalance.inventory_item_id == item_id)
        .with_for_update()
        # A row this session read earlier keeps its old quantity unless the locked read
        # replaces it; the lock is only worth having over the latest committed value.
        .execution_options(populate_existing=True)
    )
    row = db.scalars(query).first()
    if row is not None:
        return row
    try:
        with db.begin_nested():
            row = StockBalance(
                tenant_id=org.tenant_id,
                location_id=location_id,
                inventory_item_id=item_id,
                qty_base=Decimal("0"),
            )
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        # Another posting created it a moment ago; wait for it like any other.
        return db.scalars(query).one()


def _current_cost(db: Session, org: OrgContext, location_id: int, item_id: int) -> Decimal:
    """What one base unit costs now at that location (PD-08), in cents."""
    stock = db.scalars(
        select_scoped(org, StockItem).where(
            StockItem.location_id == location_id, StockItem.inventory_item_id == item_id
        )
    ).first()
    cost = Decimal(stock.cost_per_base_cents or 0) if stock is not None else Decimal("0")
    # A cost below zero is a slip in the stock row; the ledger keeps costs at zero or above
    # (DR-19), as the opening balances did (ADR-0041).
    return max(cost, Decimal("0"))


def _project(db: Session, org: OrgContext, location_id: int, item_id: int, qty: Decimal) -> None:
    """Keep the stock row every screen reads equal to the balance (ADR-0042)."""
    for stock in db.scalars(
        select_scoped(org, StockItem).where(
            StockItem.location_id == location_id, StockItem.inventory_item_id == item_id
        )
    ):
        stock.qty_base = qty


def post_transaction(
    db: Session,
    org: OrgContext,
    *,
    kind: str,
    source_type: str,
    source_id: str,
    idempotency_key: str,
    lines: Sequence[Line],
    occurred_at: datetime,
    actor_user_id: int | None | _Unset = _MEMBER,
    reason: str | None = None,
    revision: int = 0,
    reverses_transaction_id: int | None = None,
    refuse_below_zero: bool = False,
) -> InventoryTransaction:
    """Post one business event's movements, or return the earlier posting of the same key.

    A manual movement passes refuse_below_zero: it is refused with BelowZero if it would take
    a balance below zero, checked while the balance is locked (DR-08). Movements the system
    observed (a sale, a count) post in full even when stock goes negative."""
    key = idempotency_key.strip()
    accepted = check_transaction(kind, lines, key)
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise LedgerRuleError("occurred_at must say its time zone")
    if (kind == "reversal") != (reverses_transaction_id is not None):
        raise LedgerRuleError("only a reversal names the transaction it undoes, and it must")
    earlier = _by_key(db, org, key)
    if earlier is not None:
        return earlier
    pairs = sorted(
        {(line.location_id, line.inventory_item_id) for line in accepted},
        key=lambda pair: (pair[1], pair[0]),
    )
    _check_stocked(db, org, pairs)
    actor = org.user_id if isinstance(actor_user_id, _Unset) else actor_user_id
    try:
        with db.begin_nested():
            balances = {pair: _locked_balance(db, org, *pair) for pair in pairs}
            if refuse_below_zero:
                held = {pair: Decimal(row.qty_base) for pair, row in balances.items()}
                if crosses_zero(held, accepted):
                    raise BelowZero("That would take recorded stock below zero.")
            transaction = InventoryTransaction(
                tenant_id=org.tenant_id,
                location_id=accepted[0].location_id,
                kind=kind,
                source_type=source_type[:40],
                source_id=str(source_id)[:80],
                revision=revision,
                idempotency_key=key,
                actor_user_id=actor,
                reason=reason[:300] if reason else None,
                occurred_at=occurred_at,
                reverses_transaction_id=reverses_transaction_id,
            )
            db.add(transaction)
            db.flush()
            now = datetime.now(UTC)
            for line in accepted:
                cost = line.unit_cost
                if cost is None:
                    cost = _current_cost(db, org, line.location_id, line.inventory_item_id)
                movement = InventoryMovement(
                    tenant_id=org.tenant_id,
                    transaction_id=transaction.id,
                    location_id=line.location_id,
                    inventory_item_id=line.inventory_item_id,
                    batch_id=line.batch_id,
                    allocation_mode="explicit" if line.batch_id is not None else "none",
                    movement_type=line.movement_type,
                    qty_base=line.qty_base,
                    unit_cost_snapshot=cost,
                    reason=line.reason[:300] if line.reason else None,
                    reverses_movement_id=line.reverses_movement_id,
                    occurred_at=occurred_at,
                )
                db.add(movement)
                db.flush()
                balance = balances[(line.location_id, line.inventory_item_id)]
                balance.qty_base = Decimal(balance.qty_base) + line.qty_base
                balance.last_movement_id = movement.id
                balance.updated_at = now
            for (location_id, item_id), balance in balances.items():
                _project(db, org, location_id, item_id, Decimal(balance.qty_base))
            db.flush()
    except IntegrityError:
        # The same key was posted by someone else between our look and our write (DR-05).
        earlier = _by_key(db, org, key)
        if earlier is not None:
            return earlier
        raise
    return transaction


def post_opening(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    inventory_item_id: int,
    qty_base: Decimal,
    source_type: str,
    source_id: str,
    occurred_at: datetime,
    actor_user_id: int | None | _Unset = _MEMBER,
) -> InventoryTransaction | None:
    """Open a newly stocked item at a location with the quantity it starts with (DR-03, ADR-0042).
    None for nothing: a zero is the balance every pair starts at, and no movement is zero."""
    qty = places(qty_base)
    if qty == 0:
        return None
    return post_transaction(
        db,
        org,
        kind="opening",
        source_type=source_type,
        source_id=source_id,
        idempotency_key=f"opening:{inventory_item_id}:{location_id}",
        lines=[Line(location_id, inventory_item_id, "opening_balance", qty)],
        occurred_at=occurred_at,
        actor_user_id=actor_user_id,
        reason="Opening quantity entered when the item was stocked here",
    )


def post_count(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    inventory_item_id: int,
    counted: Decimal,
    source_type: str,
    source_id: str,
    idempotency_key: str,
    occurred_at: datetime,
    actor_user_id: int | None | _Unset = _MEMBER,
    reason: str | None = None,
) -> InventoryTransaction | None:
    """Record a count as an observation (DR-12): post the difference from the recorded balance
    as one count_variance, worked out while that balance is locked, so a posting that lands
    between reading and writing is not lost. None when the count matches the record."""
    key = idempotency_key.strip()
    earlier = _by_key(db, org, key)
    if earlier is not None:
        return earlier
    _check_stocked(db, org, [(location_id, inventory_item_id)])
    # Held until the request commits; post_transaction takes the same lock again.
    held = _locked_balance(db, org, location_id, inventory_item_id)
    variance = places(counted) - Decimal(held.qty_base)
    if variance == 0:
        return None
    return post_transaction(
        db,
        org,
        kind="count",
        source_type=source_type,
        source_id=source_id,
        idempotency_key=key,
        lines=[Line(location_id, inventory_item_id, "count_variance", variance)],
        occurred_at=occurred_at,
        actor_user_id=actor_user_id,
        reason=reason,
    )


def reverse_transaction(
    db: Session,
    org: OrgContext,
    transaction_id: int,
    *,
    reason: str,
    occurred_at: datetime | None = None,
    actor_user_id: int | None | _Unset = _MEMBER,
) -> InventoryTransaction:
    """Undo a posting with an equal and opposite one that names it (DR-02, WF-26). Asking
    twice returns the first reversal."""
    original = db.scalars(
        select_scoped(org, InventoryTransaction).where(InventoryTransaction.id == transaction_id)
    ).first()
    if original is None:
        raise NotFound("transaction not found")
    if original.kind == "reversal":
        raise LedgerRuleError("a reversal is not reversed again: post an adjustment instead")
    lines = [
        Line(
            location_id=movement.location_id,
            inventory_item_id=movement.inventory_item_id,
            movement_type="reversal",
            qty_base=reversal_of(movement.movement_type, Decimal(movement.qty_base)),
            unit_cost=Decimal(movement.unit_cost_snapshot),
            batch_id=movement.batch_id,
            reason=reason,
            reverses_movement_id=movement.id,
        )
        for movement in original.movements
    ]
    return post_transaction(
        db,
        org,
        kind="reversal",
        source_type=original.source_type,
        source_id=original.source_id,
        idempotency_key=f"reversal:{original.id}",
        lines=lines,
        occurred_at=occurred_at or datetime.now(UTC),
        actor_user_id=actor_user_id,
        reason=reason,
        revision=original.revision + 1,
        reverses_transaction_id=original.id,
    )


def reversed_by(db: Session, org: OrgContext, transaction_id: int) -> InventoryTransaction | None:
    """The reversal of a posting, if there is one: how a source record shows it was corrected
    (DR-06)."""
    return db.scalars(
        select_scoped(org, InventoryTransaction).where(
            InventoryTransaction.reverses_transaction_id == transaction_id
        )
    ).first()


def balance(db: Session, org: OrgContext, location_id: int, item_id: int) -> Decimal:
    """The recorded quantity (the projection); zero before the first movement."""
    row = db.scalars(
        select_scoped(org, StockBalance).where(
            StockBalance.location_id == location_id, StockBalance.inventory_item_id == item_id
        )
    ).first()
    return Decimal(row.qty_base) if row is not None else Decimal("0")


def movements_total(db: Session, org: OrgContext, location_id: int, item_id: int) -> Decimal:
    """The sum of the movements themselves, which the projection must equal (DR-03)."""
    rows = db.scalars(
        select_scoped(org, InventoryMovement).where(
            InventoryMovement.location_id == location_id,
            InventoryMovement.inventory_item_id == item_id,
        )
    )
    return sum((Decimal(row.qty_base) for row in rows), Decimal("0"))


def has_moved(db: Session, org: OrgContext, location_id: int, item_id: int) -> bool:
    """Whether anything was ever posted for this item at this location."""
    first = db.scalars(
        select_scoped(org, InventoryMovement)
        .where(
            InventoryMovement.location_id == location_id,
            InventoryMovement.inventory_item_id == item_id,
        )
        .limit(1)
    ).first()
    return first is not None
