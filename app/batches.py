"""Batches: the dated lots an item's stock is in (T2.06; BAT-FR-01..08, DR-13..15, ADR-0056).

A batch holds the sum of the ledger movements that name it. A receipt with a date creates its
batch and receives into it; stock already on the shelf is dated by moving that much of the
item's undated stock into a new batch, net zero for the item. A batch is never deleted (DR-24):
a wrong date is undone by moving what the batch holds back to undated stock, and the batch stays
with its history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.ledger.history import HistoryRow, batch_history
from app.ledger.posting import (
    assign_batch,
    balance,
    batch_remaining,
    held_batches,
    post_batch_count,
    posted_count,
)
from app.models import InventoryItem, Location, StockBatch, StockItem
from app.transform.batches import BatchRuleError, status, undated
from app.transform.business_days import business_date
from app.transform.exceptions import Finding
from app.transform.ledger import places
from app.units import format_qty

SOURCE = "batch"


class NotFound(LookupError):
    """A batch outside the business, or not there at all."""


def undated_stock(db: Session, org: OrgContext, stock: StockItem) -> Decimal:
    """What no batch of this item at this location holds (DR-13)."""
    if stock.inventory_item_id is None:
        return Decimal("0")
    held = held_batches(db, org, stock.location_id, stock.inventory_item_id)
    return undated(balance(db, org, stock.location_id, stock.inventory_item_id), held)


def new_batch(
    db: Session,
    org: OrgContext,
    stock: StockItem,
    qty_base: Decimal,
    expires_on: date,
    *,
    source: str,
    date_type: str = "unknown",
    date_confidence: Decimal | None = None,
    cost_per_base_cents: Decimal | None = None,
    document_line_id: int | None = None,
    received_at: datetime | None = None,
) -> StockBatch:
    """A batch row for stock arriving now; the movement that fills it names it (BAT-FR-01)."""
    batch = StockBatch(
        tenant_id=org.tenant_id,
        stock_item_id=stock.id,
        received_qty_base=places(qty_base),
        expires_on=expires_on,
        date_type=date_type,
        date_confidence=date_confidence,
        cost_per_base_cents=cost_per_base_cents,
        source=source,
        document_line_id=document_line_id,
        received_at=received_at or datetime.now(UTC),
    )
    db.add(batch)
    db.flush()
    return batch


def date_stock(
    db: Session,
    org: OrgContext,
    stock: StockItem,
    qty_base: Decimal,
    expires_on: date,
    *,
    source: str = "label",
    date_type: str = "unknown",
    now: datetime | None = None,
) -> StockBatch:
    """Give a date to stock already on the shelf: a new batch takes that much of the item's
    undated stock. The item's quantity does not change."""
    if stock.inventory_item_id is None:
        raise BatchRuleError("That stock is not linked to an item.")
    qty = places(qty_base)
    if qty <= 0:
        raise BatchRuleError("Enter a quantity above zero.")
    if qty > undated_stock(db, org, stock):
        raise BatchRuleError("That is more than the stock without a date.")
    now = now or datetime.now(UTC)
    batch = new_batch(
        db,
        org,
        stock,
        qty,
        expires_on,
        source=source,
        date_type=date_type,
        cost_per_base_cents=Decimal(stock.cost_per_base_cents)
        if stock.cost_per_base_cents
        else None,
        received_at=now,
    )
    assign_batch(
        db,
        org,
        location_id=stock.location_id,
        inventory_item_id=stock.inventory_item_id,
        qty_base=qty,
        to_batch_id=batch.id,
        from_batch_id=None,
        source_type=SOURCE,
        source_id=str(batch.id),
        idempotency_key=f"batch:{batch.id}:dated",
        occurred_at=now,
        reason="Dated stock already on the shelf",
    )
    return batch


def undate_batch(
    db: Session, org: OrgContext, batch_id: int, *, now: datetime | None = None
) -> StockBatch:
    """Take a wrongly entered date off: what the batch holds goes back to undated stock. The
    batch stays, with its history (DR-24); the item's quantity does not change."""
    batch = get_scoped(db, org, StockBatch, batch_id)
    if batch is None:
        raise NotFound(batch_id)
    stock = get_scoped(db, org, StockItem, batch.stock_item_id)
    if stock is None or stock.inventory_item_id is None:
        raise NotFound(batch_id)
    held = batch_remaining(db, org, [batch.id]).get(batch.id, Decimal("0"))
    if held > 0:
        done = posted_count(db, org, SOURCE, str(batch.id))
        assign_batch(
            db,
            org,
            location_id=stock.location_id,
            inventory_item_id=stock.inventory_item_id,
            qty_base=held,
            to_batch_id=None,
            from_batch_id=batch.id,
            source_type=SOURCE,
            source_id=str(batch.id),
            idempotency_key=f"batch:{batch.id}:undated:{done}",
            occurred_at=now or datetime.now(UTC),
            reason="Date removed: entered by mistake",
        )
    return batch


@dataclass(frozen=True)
class BatchView:
    """One batch as its page shows it: what it holds, its status, and every movement."""

    batch: StockBatch
    stock: StockItem
    location: Location
    remaining: Decimal
    status: str
    history: list[HistoryRow]
    item_type: str = "raw"


def _batch_and_stock(db: Session, org: OrgContext, batch_id: int) -> tuple[StockBatch, StockItem]:
    batch = get_scoped(db, org, StockBatch, batch_id)
    stock = get_scoped(db, org, StockItem, batch.stock_item_id) if batch is not None else None
    if batch is None or stock is None or stock.inventory_item_id is None:
        raise NotFound(batch_id)
    return batch, stock


def batch_view(
    db: Session, org: OrgContext, batch_id: int, now: datetime | None = None
) -> BatchView:
    """A batch's page (BAT-FR-12, AC-BAT-08): what it holds now, its status at its location's
    today, and every movement allocated to it with what it held after each."""
    batch, stock = _batch_and_stock(db, org, batch_id)
    place = get_scoped(db, org, Location, stock.location_id)
    if place is None:
        raise NotFound(batch_id)
    held = batch_remaining(db, org, [batch.id]).get(batch.id, Decimal("0"))
    today = business_date(now or datetime.now(UTC), place.timezone, place.business_day_cutoff_hour)
    item = (
        get_scoped(db, org, InventoryItem, stock.inventory_item_id)
        if stock.inventory_item_id is not None
        else None
    )
    return BatchView(
        batch,
        stock,
        place,
        held,
        status(held, batch.expires_on, today),
        batch_history(db, org, place, batch.id),
        item.item_type if item is not None else "raw",
    )


def count_batch(
    db: Session,
    org: OrgContext,
    batch_id: int,
    counted: Decimal,
    *,
    now: datetime | None = None,
) -> Decimal:
    """Count what one batch holds (BAT-FR-13): the difference posts as a count variance on that
    batch, so the item's stock moves with it. Returns the difference (zero when it matched)."""
    if counted < 0:
        raise BatchRuleError("A count cannot be below zero.")
    batch, stock = _batch_and_stock(db, org, batch_id)
    if stock.inventory_item_id is None:  # _batch_and_stock refuses these; for the types
        raise NotFound(batch_id)
    before = batch_remaining(db, org, [batch.id]).get(batch.id, Decimal("0"))
    post_batch_count(
        db,
        org,
        location_id=stock.location_id,
        inventory_item_id=stock.inventory_item_id,
        batch_id=batch.id,
        counted=counted,
        idempotency_key=f"batch:{batch.id}:count:{uuid4().hex}",
        occurred_at=now or datetime.now(UTC),
        reason="Batch counted",
    )
    return places(counted) - before


def shortfalls(db: Session, org: OrgContext) -> list[Finding]:
    """DR-13: for an item that tracks batches, stock that no batch holds is explicit. Undated
    stock (a delivery with no date, a count that found more) and stock taken beyond every batch
    are each a `batch_shortfall` on the item at that location, until it is dated or counted."""
    tracked = {
        item.id: item
        for item in db.scalars(
            select_scoped(org, InventoryItem).where(InventoryItem.tracks_batches.is_(True))
        )
    }
    if not tracked:
        return []
    places_by_id = {place.id: place for place in db.scalars(select_scoped(org, Location))}
    found = []
    for stock in db.scalars(
        select_scoped(org, StockItem).where(StockItem.inventory_item_id.in_(list(tracked)))
    ):
        item = tracked[stock.inventory_item_id] if stock.inventory_item_id else None
        place = places_by_id.get(stock.location_id)
        if item is None or place is None:
            continue
        held = sum((h.remaining for h in held_batches(db, org, place.id, item.id)), Decimal("0"))
        loose = balance(db, org, place.id, item.id) - held
        if loose == 0:
            continue
        amount = format_qty(abs(loose), item.display_unit)
        title = (
            f"{amount} of {item.name} at {place.name} is in no batch: date it or count it."
            if loose > 0
            else f"{item.name} at {place.name} used {amount} more than its batches held: count it."
        )
        found.append(
            Finding(
                type="batch_shortfall",
                subject_type="item_location",
                subject_id=f"{item.id}:{place.id}",
                title=title,
                location_id=place.id,
                quantity_base=loose,
            )
        )
    return found
