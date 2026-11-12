"""Nightly ledger verification (T1.21; DR-03, REL-08, AC-INV-11, OBS-06).

Recomputes, for one organization, what every item's movements add up to at each location, and
compares it with the recorded balance and with the stock row every screen reads (its projection,
ADR-0042). It only reads: a drift opens a critical `balance_drift` exception and is reported for
the founder, and neither figure is changed (DR-03: never auto-overwrite).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.models import InventoryItem, InventoryMovement, Location, StockBalance, StockItem
from app.transform.exceptions import Finding
from app.transform.ledger import Drift, find_drift
from app.units import format_qty

log = logging.getLogger(__name__)

BATCH = 2000


def drifts(db: Session, org: OrgContext) -> list[Drift]:
    """Every item at every location whose balance, movements and stock row disagree."""
    movements: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0"))
    rows = db.execute(
        select_scoped(org, InventoryMovement)
        .with_only_columns(
            InventoryMovement.location_id,
            InventoryMovement.inventory_item_id,
            InventoryMovement.qty_base,
        )
        .execution_options(yield_per=BATCH)
    )
    # Summed here, not in SQL: SQLite keeps quantities as text and would add them as floats.
    for location_id, item_id, qty in rows:
        movements[(location_id, item_id)] += Decimal(qty)
    recorded = {
        (row.location_id, row.inventory_item_id): Decimal(row.qty_base)
        for row in db.scalars(select_scoped(org, StockBalance))
    }
    shown = {
        (row.location_id, row.inventory_item_id): Decimal(row.qty_base or 0)
        for row in db.scalars(
            select_scoped(org, StockItem).where(StockItem.inventory_item_id.is_not(None))
        )
        if row.inventory_item_id is not None
    }
    return find_drift(recorded, dict(movements), shown)


def findings(db: Session, org: OrgContext, found: list[Drift]) -> list[Finding]:
    """Each drift as the exception it opens, named and in the item's own unit."""
    if not found:
        return []
    items = {item.id: item for item in db.scalars(select_scoped(org, InventoryItem))}
    places = {place.id: place.name for place in db.scalars(select_scoped(org, Location))}
    result = []
    for drift in found:
        item = items.get(drift.inventory_item_id)
        unit = item.base_unit if item else "each"
        name = item.name if item else f"Item {drift.inventory_item_id}"
        place = places.get(drift.location_id, f"location {drift.location_id}")
        title = (
            f"{name} at {place}: the recorded balance is {format_qty(drift.recorded, unit)} "
            f"but its movements add up to {format_qty(drift.movements, unit)}"
        )
        if drift.shown is not None and drift.shown != drift.recorded:
            title += f", and the screens show {format_qty(drift.shown, unit)}"
        result.append(
            Finding(
                type="balance_drift",
                subject_type="item_location",
                subject_id=f"{drift.inventory_item_id}:{drift.location_id}",
                title=title + ". Nothing was changed.",
                location_id=drift.location_id,
                quantity_base=drift.movements - drift.recorded,
            )
        )
    return result
