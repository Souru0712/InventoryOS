"""Manual adjustments (T1.22; INV-FR-19, INV-FR-23, DR-08, WF-10, AC-INV-08, -09).

The only writer of inventory_adjustments. An adjustment is a source record with a reason; its
quantity reaches stock only through one `adjustment` posting keyed `adjustment:<id>`. The ledger
refuses one that would take stock below zero unless the person may override and says why; then
it posts and a `negative_balance` exception opens. One worth the review threshold or more posts
and opens an `adjustment_review` exception for an Owner or Administrator (STF-FR-08, R1).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.exceptions import record_exception
from app.ledger.posting import balance, post_transaction
from app.models import InventoryAdjustment, InventoryItem, ItemLocation, Location
from app.transform.adjustments import (
    REASONS,
    AdjustmentRuleError,
    check_adjustment,
    needs_review,
    value_cents,
)
from app.transform.exceptions import Finding
from app.transform.ledger import Line, places
from app.transform.reports import money
from app.units import format_change, format_qty

log = logging.getLogger(__name__)

OVERRIDE = "adjustment.override_negative"


class NotFound(LookupError):
    """No such item stocked at that location that org may see."""


def negative_finding(item: InventoryItem, location: Location, qty: Decimal) -> Finding:
    """Recorded stock below zero: someone needs to count it (DR-08, AC-INV-10)."""
    return Finding(
        type="negative_balance",
        subject_type="item_location",
        subject_id=f"{item.id}:{location.id}",
        title=(
            f"{item.name} at {location.name} is below zero "
            f"({format_qty(qty, item.display_unit)}): count required."
        ),
        location_id=location.id,
        quantity_base=qty,
    )


def adjust(
    db: Session,
    org: OrgContext,
    *,
    inventory_item_id: int,
    location_id: int,
    qty_base: Decimal,
    reason: str,
    note: str | None = None,
    override: bool = False,
    override_reason: str | None = None,
    now: datetime | None = None,
) -> InventoryAdjustment:
    """Post one adjustment. Refused with AdjustmentRuleError, or with BelowZero when it would
    take stock below zero and the person may not, or did not, override; the caller then rolls
    back, and the adjustment row goes with it."""
    if override and not org.can(OVERRIDE):
        raise AdjustmentRuleError("Only a Manager or above may take stock below zero.")
    checked = check_adjustment(
        places(qty_base), reason, note, override=override, override_reason=override_reason
    )
    item = get_scoped(db, org, InventoryItem, inventory_item_id)
    location = get_scoped(db, org, Location, location_id)
    stocked = db.scalars(
        select_scoped(org, ItemLocation).where(
            ItemLocation.inventory_item_id == inventory_item_id,
            ItemLocation.location_id == location_id,
        )
    ).first()
    if item is None or location is None or stocked is None:
        raise NotFound("item not stocked there")
    now = now or datetime.now(UTC)

    row = InventoryAdjustment(
        tenant_id=org.tenant_id,
        location_id=location.id,
        inventory_item_id=item.id,
        qty_base=checked.qty_base,
        reason=checked.reason,
        note=checked.note,
        override_reason=checked.override_reason,
        actor_user_id=org.user_id,
    )
    db.add(row)
    db.flush()
    why = REASONS[checked.reason] + (f": {checked.note}" if checked.note else "")
    if checked.override_reason:
        why += f" (below zero allowed: {checked.override_reason})"
    posted = post_transaction(
        db,
        org,
        kind="adjustment",
        source_type="adjustment",
        source_id=str(row.id),
        idempotency_key=f"adjustment:{row.id}",
        lines=[Line(location.id, item.id, "adjustment", checked.qty_base, reason=why)],
        occurred_at=now,
        reason=why,
        refuse_below_zero=not override,
    )
    unit_cost = Decimal(posted.movements[0].unit_cost_snapshot)
    row.transaction_id = posted.id
    row.value_cents = value_cents(checked.qty_base, unit_cost)

    after = balance(db, org, location.id, item.id)
    if after < 0:
        record_exception(db, org, negative_finding(item, location, after), now=now)
    if needs_review(checked.qty_base, unit_cost):
        record_exception(
            db,
            org,
            Finding(
                type="adjustment_review",
                subject_type="adjustment",
                subject_id=str(row.id),
                title=(
                    f"{format_change(checked.qty_base, item.display_unit)} of {item.name} at "
                    f"{location.name} (worth {money(row.value_cents, location.currency)}) was "
                    f"adjusted. Reason: {why}. An Owner or Administrator reviews it."
                ),
                location_id=location.id,
                money_impact_cents=row.value_cents,
            ),
            now=now,
        )
    db.flush()
    log.info("adjustment %s posted for item %s at location %s", row.id, item.id, location.id)
    return row
