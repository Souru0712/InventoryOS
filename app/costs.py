"""Current costs, their history, and valuation (T1.24; ANL-FR-01, -02, INV-FR-07, -24, DR-19).

The only writer of item_cost_history and of a stock row's current cost. A receipt sets the cost
it paid; someone with `receiving.edit_costs` may override it; either way one entry is appended
(never edited) and the stock row mirrors the latest. Movements keep the cost they were posted at,
so past figures never change when today's cost does (PD-08, AC-ANL-01). Valuation is
Σ max(recorded, 0) × current cost, now or as of a date, and says so (AC-ANL-02).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.audit import record_audit
from app.ledger.history import snapshot_cost_at
from app.ledger.posting import balance_at
from app.models import ItemCostHistory, Location, StockItem
from app.transform.business_days import business_day_end
from app.transform.costs import (
    CostRuleError,
    Holding,
    Valuation,
    check_cost,
    cost_at,
    valuation,
)

log = logging.getLogger(__name__)


class NotFound(LookupError):
    """No such item stocked at that location that org may see."""


def _stock(db: Session, org: OrgContext, location_id: int, item_id: int) -> StockItem:
    row = db.scalars(
        select_scoped(org, StockItem).where(
            StockItem.location_id == location_id, StockItem.inventory_item_id == item_id
        )
    ).first()
    if row is None:
        raise NotFound("item not stocked there")
    return row


def history(db: Session, org: OrgContext, location_id: int, item_id: int) -> list[ItemCostHistory]:
    """The item's costs at the location, newest first."""
    return list(
        db.scalars(
            select_scoped(org, ItemCostHistory)
            .where(
                ItemCostHistory.location_id == location_id,
                ItemCostHistory.inventory_item_id == item_id,
            )
            .order_by(ItemCostHistory.effective_at.desc(), ItemCostHistory.id.desc())
        )
    )


def set_cost(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    inventory_item_id: int,
    cost: Decimal,
    source_type: str,
    source_id: str | None = None,
    effective_at: datetime | None = None,
) -> ItemCostHistory | None:
    """Make this the item's current cost at the location (INV-FR-07): one history entry and the
    stock row. Nothing is written when the cost is already that and has a history."""
    cost = check_cost(Decimal(cost))
    stock = _stock(db, org, location_id, inventory_item_id)
    known = bool(history(db, org, location_id, inventory_item_id))
    if known and Decimal(stock.cost_per_base_cents or 0) == cost:
        return None
    entry = ItemCostHistory(
        tenant_id=org.tenant_id,
        location_id=location_id,
        inventory_item_id=inventory_item_id,
        cost_per_base_cents=cost,
        source_type=source_type,
        source_id=source_id,
        actor_user_id=org.user_id,
        effective_at=effective_at or datetime.now(UTC),
    )
    db.add(entry)
    stock.cost_per_base_cents = cost
    db.flush()
    return entry


def override_cost(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    inventory_item_id: int,
    cost: Decimal,
    ip: str | None = None,
) -> ItemCostHistory | None:
    """Someone sets the current cost by hand; it stays until the next receipt (INV-FR-07)."""
    stock = _stock(db, org, location_id, inventory_item_id)
    was = Decimal(stock.cost_per_base_cents or 0)
    entry = set_cost(
        db,
        org,
        location_id=location_id,
        inventory_item_id=inventory_item_id,
        cost=cost,
        source_type="override",
    )
    if entry is not None:
        record_audit(
            db,
            org,
            "item.cost_changed",
            f"Changed the current cost of {stock.name}",
            subject_type="item_location",
            subject_id=stock.item_location_id,
            location_id=location_id,
            before={"cost_per_base_cents": str(was)},
            after={"cost_per_base_cents": str(entry.cost_per_base_cents)},
            ip=ip,
        )
    return entry


def cost_on(
    db: Session, org: OrgContext, location_id: int, item_id: int, at: datetime
) -> Decimal | None:
    """The cost in effect at an instant: from the history, or, before the item had one, from
    the last movement posted by then."""
    entries = [
        (row.effective_at, Decimal(row.cost_per_base_cents))
        for row in history(db, org, location_id, item_id)
    ]
    found = cost_at(entries, at)
    if found is not None:
        return found
    return snapshot_cost_at(db, org, location_id, item_id, at)


def value_stock(
    db: Session,
    org: OrgContext,
    *,
    location_id: int | None = None,
    as_of_day: date | None = None,
) -> Valuation:
    """Inventory value now (recorded × current cost), or at the end of a business day at each
    location (what was held then × the cost in effect then), item by item, with the items held
    without a cost (ANL-FR-02)."""
    query = select_scoped(org, StockItem).where(StockItem.inventory_item_id.is_not(None))
    if location_id is not None:
        query = query.where(StockItem.location_id == location_id)
    ends = {}
    if as_of_day is not None:
        ends = {
            place.id: business_day_end(as_of_day, place.timezone, place.business_day_cutoff_hour)
            for place in db.scalars(select_scoped(org, Location))
        }
    holdings = []
    for stock in db.scalars(query):
        item_id = stock.inventory_item_id
        if item_id is None:
            continue
        as_of = ends.get(stock.location_id)
        if as_of is None:
            qty = Decimal(stock.qty_base or 0)
            cost: Decimal | None = Decimal(stock.cost_per_base_cents or 0)
        else:
            qty = balance_at(db, org, stock.location_id, item_id, as_of)
            cost = cost_on(db, org, stock.location_id, item_id, as_of)
        holdings.append(
            Holding(
                stock.location_id,
                item_id,
                stock.name,
                qty,
                cost if cost else None,
                stock.stock_unit,
            )
        )
    return valuation(holdings)


__all__ = [
    "CostRuleError",
    "NotFound",
    "cost_on",
    "history",
    "override_cost",
    "set_cost",
    "value_stock",
]
