"""Movement history (T1.22; INV-FR-20, AC-INV-08): what moved one item at one location, by
whom, why, and the balance after each movement.

Read-only. The running balance is worked out over every movement in posting order, the order
the balance itself moved in, before any filter is applied, so a filtered view still shows true
balances.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.models import InventoryMovement, InventoryTransaction, Location
from app.transform.business_days import business_date


@dataclass(frozen=True)
class HistoryRow:
    movement_id: int
    occurred_at: datetime
    business_day: date
    kind: str  # the transaction's kind: receipt, count, adjustment...
    movement_type: str
    qty_base: Decimal
    running_base: Decimal  # the balance after this movement
    unit_cost: Decimal  # cents per base unit, as posted
    actor_user_id: int | None
    reason: str | None
    source_type: str
    source_id: str


def history(
    db: Session,
    org: OrgContext,
    location: Location,
    inventory_item_id: int,
    *,
    movement_type: str | None = None,
    since: date | None = None,
    until: date | None = None,
    actor_user_id: int | None = None,
) -> list[HistoryRow]:
    """The item's movements at the location, newest first, each with the balance after it.
    Dates are the location's business days (ADR-0027)."""
    rows = db.execute(
        select_scoped(org, InventoryMovement)
        .add_columns(InventoryTransaction)
        .join(InventoryTransaction, InventoryTransaction.id == InventoryMovement.transaction_id)
        .where(
            InventoryMovement.location_id == location.id,
            InventoryMovement.inventory_item_id == inventory_item_id,
        )
        .order_by(InventoryMovement.id)
    ).all()
    running = Decimal("0")
    found: list[HistoryRow] = []
    for movement, transaction in rows:
        running += Decimal(movement.qty_base)
        found.append(
            HistoryRow(
                movement_id=movement.id,
                occurred_at=movement.occurred_at,
                business_day=business_date(
                    movement.occurred_at, location.timezone, location.business_day_cutoff_hour
                ),
                kind=transaction.kind,
                movement_type=movement.movement_type,
                qty_base=Decimal(movement.qty_base),
                running_base=running,
                unit_cost=Decimal(movement.unit_cost_snapshot),
                actor_user_id=transaction.actor_user_id,
                reason=movement.reason or transaction.reason,
                source_type=transaction.source_type,
                source_id=transaction.source_id,
            )
        )
    shown = [
        row
        for row in found
        if (movement_type is None or row.movement_type == movement_type)
        and (since is None or row.business_day >= since)
        and (until is None or row.business_day <= until)
        and (actor_user_id is None or row.actor_user_id == actor_user_id)
    ]
    return list(reversed(shown))
