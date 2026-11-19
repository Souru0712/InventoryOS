"""Receivings: deliveries received and posted to stock (T2.11-a; RCV-FR-01, -05..07, -09..12, DR-07,
DM-12, ADR-0062).

The only writer of `receivings` and `receiving_lines`. A receiving starts as a draft with lines
(invoiced, arrived, accepted, price, expiry); posting it happens exactly once, by a conditional
change of its status, even when two people press post together (AC-RCV-05). Each accepted line
then posts its own `receipt` transaction keyed `receiving_line:<id>`, into a new batch when it has
a date, at its price; the price becomes the item's cost and the supplier item's last price, and a
price far from the last one is flagged (RCV-FR-06, -11). Discrepancies (short, over, rejected)
are kept on the line and open `receiving_discrepancy`. A posted line is corrected by reversing it
and posting a new line that names it (RCV-FR-12, AC-RCV-08).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped, update_scoped
from app.batches import new_batch
from app.costs import set_cost
from app.exceptions import record_exception
from app.items import entered_base
from app.ledger.posting import post_transaction, reverse_transaction
from app.models import (
    Document,
    DocumentLine,
    InventoryItem,
    ItemLocation,
    Location,
    Receiving,
    ReceivingLine,
    StockItem,
    Supplier,
    SupplierItem,
)
from app.suppliers import record_price
from app.transform.exceptions import Finding
from app.transform.items import ItemRuleError
from app.transform.ledger import Line, places
from app.transform.receivings import Discrepancies, ReceivingRuleError, check_quantities
from app.transform.suppliers import price_change

SOURCE = "receiving_line"
EDIT_COSTS = "receiving.edit_costs"


class NotFound(LookupError):
    """A receiving or line outside the business, or not there at all."""


class AlreadyPosted(ReceivingRuleError):
    """The receiving was posted already (by this request's twin, or earlier)."""


def get_receiving(db: Session, org: OrgContext, receiving_id: int) -> Receiving:
    row = get_scoped(db, org, Receiving, receiving_id)
    if row is None:
        raise NotFound(receiving_id)
    return row


def list_receivings(db: Session, org: OrgContext, limit: int = 50) -> list[Receiving]:
    return list(
        db.scalars(select_scoped(org, Receiving).order_by(Receiving.id.desc()).limit(limit))
    )


def start_receiving(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    supplier_id: int | None = None,
    note: str = "",
) -> Receiving:
    """A draft delivery to add lines to (RCV-FR-01, -08)."""
    place = get_scoped(db, org, Location, location_id)
    if place is None:
        raise NotFound(location_id)
    if supplier_id is not None and get_scoped(db, org, Supplier, supplier_id) is None:
        raise NotFound(supplier_id)
    row = Receiving(
        tenant_id=org.tenant_id,
        location_id=place.id,
        supplier_id=supplier_id,
        note=note.strip()[:300] or None,
        created_by_user_id=org.user_id,
    )
    db.add(row)
    db.flush()
    return row


def _decimal(raw: str | None, what: str) -> Decimal | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return Decimal(str(raw).strip())
    except InvalidOperation as exc:
        raise ReceivingRuleError(f"{raw} is not a {what}.") from exc


def add_line(
    db: Session,
    org: OrgContext,
    receiving_id: int,
    *,
    inventory_item_id: int,
    accepted: str,
    unit: str = "",
    invoiced: str | None = None,
    arrived: str | None = None,
    unit_price: str | None = None,
    supplier_item_id: int | None = None,
    expires_on: date | None = None,
    lot_code: str = "",
    reject_reason: str = "",
    description: str = "",
) -> ReceivingLine:
    """A line of a draft: arrived defaults to invoiced, accepted to arrived (RCV-FR-05). A unit
    of the supplier item converts by its pack (AC-PRC-02); any other through the item's own."""
    receiving = get_receiving(db, org, receiving_id)
    if receiving.status != "draft":
        raise AlreadyPosted("That delivery is posted: correct a line instead.")
    item = get_scoped(db, org, InventoryItem, inventory_item_id)
    if item is None:
        raise NotFound(inventory_item_id)
    supplier_item = None
    if supplier_item_id is not None:
        supplier_item = get_scoped(db, org, SupplierItem, supplier_item_id)
        if supplier_item is None or supplier_item.inventory_item_id != item.id:
            raise NotFound(supplier_item_id)
    accepted_qty = _decimal(accepted, "quantity")
    invoiced_qty = _decimal(invoiced, "quantity")
    arrived_qty = _decimal(arrived, "quantity")
    if accepted_qty is None:
        accepted_qty = arrived_qty if arrived_qty is not None else invoiced_qty
    if accepted_qty is None:
        raise ReceivingRuleError("Enter how much was received.")
    if arrived_qty is None:
        arrived_qty = invoiced_qty if invoiced_qty is not None else accepted_qty
    check_quantities(invoiced_qty, arrived_qty, accepted_qty, reject_reason)
    if supplier_item is not None and (not unit or unit == supplier_item.purchase_unit):
        unit = supplier_item.purchase_unit
        qty_base = places(accepted_qty * Decimal(supplier_item.pack_size_base))
    else:
        try:
            qty_base = places(entered_base(item, str(accepted_qty), unit))
        except ItemRuleError as exc:
            raise ReceivingRuleError(str(exc)) from exc
    price = _decimal(unit_price, "price")
    line = ReceivingLine(
        tenant_id=org.tenant_id,
        receiving_id=receiving.id,
        inventory_item_id=item.id,
        supplier_item_id=supplier_item.id if supplier_item else None,
        description=description.strip()[:300] or None,
        invoiced_qty=invoiced_qty,
        arrived_qty=arrived_qty,
        accepted_qty=accepted_qty,
        unit=(unit or item.display_unit)[:40],
        qty_base=qty_base,
        unit_price_cents=int((price * 100).quantize(Decimal(1))) if price is not None else None,
        expires_on=expires_on,
        lot_code=lot_code.strip()[:80] or None,
        reject_reason=reject_reason.strip()[:200] or None,
    )
    db.add(line)
    db.flush()
    return line


def remove_line(db: Session, org: OrgContext, line_id: int) -> None:
    line = get_scoped(db, org, ReceivingLine, line_id)
    if line is None:
        raise NotFound(line_id)
    if line.status != "open":
        raise AlreadyPosted("A posted line is corrected, not removed.")
    db.delete(line)
    db.flush()


def discrepancies(line: ReceivingLine) -> Discrepancies:
    return check_quantities(
        Decimal(line.invoiced_qty) if line.invoiced_qty is not None else None,
        Decimal(line.arrived_qty),
        Decimal(line.accepted_qty),
        line.reject_reason or "rejected",
    )


def _post_line(
    db: Session, org: OrgContext, receiving: Receiving, line: ReceivingLine, now: datetime
) -> None:
    """One accepted line into stock, its price into costs (RCV-FR-09, -11)."""
    stock = db.scalars(
        select_scoped(org, StockItem).where(
            StockItem.location_id == receiving.location_id,
            StockItem.inventory_item_id == line.inventory_item_id,
        )
    ).first()
    stocked = db.scalars(
        select_scoped(org, ItemLocation).where(
            ItemLocation.location_id == receiving.location_id,
            ItemLocation.inventory_item_id == line.inventory_item_id,
        )
    ).first()
    if stock is None or stocked is None:
        item = get_scoped(db, org, InventoryItem, line.inventory_item_id)
        raise ReceivingRuleError(f"{item.name if item else 'An item'} is not stocked here.")
    qty = Decimal(line.qty_base)
    cost = None
    if line.unit_price_cents is not None and qty > 0 and Decimal(line.accepted_qty) > 0:
        per_unit = qty / Decimal(line.accepted_qty)  # base units in one entered unit
        cost = places(Decimal(line.unit_price_cents) / per_unit)
    line.status = "posted"
    if qty <= 0:
        return
    if line.expires_on is not None:
        batch = new_batch(
            db,
            org,
            stock,
            qty,
            line.expires_on,
            source="receipt",
            date_type="best_before",
            cost_per_base_cents=cost,
            received_at=now,
        )
        batch.lot_code = line.lot_code
        line.batch_id = batch.id
    posted = post_transaction(
        db,
        org,
        kind="receipt",
        source_type=SOURCE,
        source_id=str(line.id),
        idempotency_key=f"receiving_line:{line.id}",
        lines=[
            Line(
                receiving.location_id,
                line.inventory_item_id,
                "receipt",
                qty,
                unit_cost=cost,
                batch_id=line.batch_id,
            )
        ],
        occurred_at=now,
        reason="Received" + (f" from delivery {receiving.id}" if receiving.id else ""),
    )
    line.transaction_id = posted.id
    if cost is not None:
        set_cost(
            db,
            org,
            location_id=receiving.location_id,
            inventory_item_id=line.inventory_item_id,
            cost=cost,
            source_type="receipt",
            source_id=f"receiving:{receiving.id}",
            effective_at=now,
        )
    if line.supplier_item_id is not None and line.unit_price_cents is not None:
        supplier_item = get_scoped(db, org, SupplierItem, line.supplier_item_id)
        if supplier_item is not None:
            same_unit = line.unit == supplier_item.purchase_unit
            if same_unit:
                change = price_change(supplier_item.last_price_cents, line.unit_price_cents)
                line.price_flag = bool(change and change.notable)
                record_price(db, org, supplier_item.id, line.unit_price_cents, at=now)


def post_receiving(
    db: Session, org: OrgContext, receiving_id: int, *, now: datetime | None = None
) -> Receiving:
    """Post a draft exactly once (DR-07, AC-RCV-05): the status changes from draft only for the
    first request; any other gets AlreadyPosted. Nothing is posted until every line passes."""
    receiving = get_receiving(db, org, receiving_id)
    if not receiving.lines:
        raise ReceivingRuleError("Add what arrived before posting.")
    now = now or datetime.now(UTC)
    claimed = db.execute(
        update_scoped(org, Receiving)
        .where(Receiving.id == receiving.id, Receiving.status == "draft")
        .values(status="posted", posted_at=now, posted_by_user_id=org.user_id)
        .execution_options(synchronize_session="fetch")
    )
    if int(getattr(claimed, "rowcount", 0) or 0) != 1:
        raise AlreadyPosted("That delivery is already posted.")
    db.refresh(receiving)
    found = []
    for line in receiving.lines:
        if line.status != "open":
            continue
        _post_line(db, org, receiving, line, now)
        item = get_scoped(db, org, InventoryItem, line.inventory_item_id)
        name = item.name if item else "item"
        for part in discrepancies(line).describe(line.unit):
            why = (
                f" ({line.reject_reason})"
                if part.startswith("rejected") and line.reject_reason
                else ""
            )
            found.append(f"{name}: {part}{why}")
        if line.price_flag:
            found.append(f"{name}: price changed")
    if found:
        record_exception(
            db,
            org,
            Finding(
                type="receiving_discrepancy",
                subject_type="receiving",
                subject_id=str(receiving.id),
                title=f"Delivery {receiving.id}: " + "; ".join(found) + ".",
                location_id=receiving.location_id,
            ),
            now=now,
        )
    db.flush()
    return receiving


def correct_line(
    db: Session,
    org: OrgContext,
    line_id: int,
    *,
    accepted: str,
    reason: str,
    now: datetime | None = None,
) -> ReceivingLine:
    """Correct a posted line (RCV-FR-12, AC-RCV-08): its receipt is reversed, the line is marked
    corrected and a new line with the right quantity is posted, naming it."""
    line = get_scoped(db, org, ReceivingLine, line_id)
    if line is None:
        raise NotFound(line_id)
    if line.status != "posted":
        raise ReceivingRuleError("Only a posted line is corrected.")
    if not reason.strip():
        raise ReceivingRuleError("Say why the line is corrected.")
    now = now or datetime.now(UTC)
    new_accepted = _decimal(accepted, "quantity")
    if new_accepted is None or new_accepted < 0:
        raise ReceivingRuleError("Enter the right quantity, zero or more.")
    receiving = get_receiving(db, org, line.receiving_id)
    if line.transaction_id is not None:
        reverse_transaction(
            db, org, line.transaction_id, reason=f"Delivery corrected: {reason.strip()}"[:300]
        )
    line.status = "corrected"
    ratio = (
        Decimal(line.qty_base) / Decimal(line.accepted_qty)
        if Decimal(line.accepted_qty) > 0
        else None
    )
    fixed = ReceivingLine(
        tenant_id=org.tenant_id,
        receiving_id=line.receiving_id,
        inventory_item_id=line.inventory_item_id,
        supplier_item_id=line.supplier_item_id,
        description=line.description,
        invoiced_qty=line.invoiced_qty,
        arrived_qty=max(Decimal(line.arrived_qty), new_accepted),
        accepted_qty=new_accepted,
        unit=line.unit,
        qty_base=places(new_accepted * ratio)
        if ratio is not None
        else _base_of(db, org, line, new_accepted),
        unit_price_cents=line.unit_price_cents,
        expires_on=line.expires_on,
        lot_code=line.lot_code,
        reject_reason=line.reject_reason,
        corrects_line_id=line.id,
        correction_reason=reason.strip()[:300],
    )
    db.add(fixed)
    db.flush()
    _post_line(db, org, receiving, fixed, now)
    db.flush()
    return fixed


def _base_of(db: Session, org: OrgContext, line: ReceivingLine, qty: Decimal) -> Decimal:
    item = get_scoped(db, org, InventoryItem, line.inventory_item_id)
    if item is None:
        raise NotFound(line.inventory_item_id)
    try:
        return places(entered_base(item, str(qty), line.unit))
    except ItemRuleError as exc:
        raise ReceivingRuleError(str(exc)) from exc


# --- invoices (T2.11-b) ---------------------------------------------------------------------------


def record_invoice(
    db: Session,
    org: OrgContext,
    document: Document,
    received: list[tuple[DocumentLine, StockItem, Decimal, Decimal | None, bool]],
    transaction_id: int | None,
    *,
    now: datetime,
) -> Receiving:
    """The receiving record of an applied invoice, linked to the receipt it posted: its supplier,
    a line per applied line, each price checked against the supplier item (RCV-FR-06), and a cost
    changed by someone without cost rights flagged (T2.11-b). Writes no movement."""
    name = (document.supplier or "").strip().casefold()
    supplier = next(
        (s for s in db.scalars(select_scoped(org, Supplier)) if s.name.casefold() == name),
        None,
    )
    receiving = Receiving(
        tenant_id=org.tenant_id,
        location_id=document.location_id,
        supplier_id=supplier.id if supplier else None,
        document_id=document.id,
        status="posted",
        created_by_user_id=document.uploaded_by,
        posted_at=now,
        posted_by_user_id=org.user_id,
    )
    db.add(receiving)
    db.flush()
    issues = []
    for doc_line, stock, qty, cost, cost_changed in received:
        if stock.inventory_item_id is None:
            continue
        flagged = False
        if supplier is not None and cost:
            supplier_item = db.scalars(
                select_scoped(org, SupplierItem).where(
                    SupplierItem.supplier_id == supplier.id,
                    SupplierItem.inventory_item_id == stock.inventory_item_id,
                )
            ).first()
            if supplier_item is not None:
                per_unit = int((cost * Decimal(supplier_item.pack_size_base)).quantize(Decimal(1)))
                change = price_change(supplier_item.last_price_cents, per_unit)
                flagged = bool(change and change.notable)
                record_price(db, org, supplier_item.id, per_unit, at=now)
        if cost_changed and not org.can(EDIT_COSTS):
            flagged = True
            issues.append(f"{stock.name}: cost entered without cost rights")
        elif flagged:
            issues.append(f"{stock.name}: price changed")
        db.add(
            ReceivingLine(
                tenant_id=org.tenant_id,
                receiving_id=receiving.id,
                inventory_item_id=stock.inventory_item_id,
                description=doc_line.parsed_name[:300] or None,
                invoiced_qty=qty,
                arrived_qty=qty,
                accepted_qty=qty,
                unit="",
                qty_base=qty,
                expires_on=doc_line.expires_on,
                price_flag=flagged,
                status="posted",
                transaction_id=transaction_id,
            )
        )
    if issues:
        record_exception(
            db,
            org,
            Finding(
                type="receiving_discrepancy",
                subject_type="receiving",
                subject_id=str(receiving.id),
                title=f"Delivery {receiving.id}: " + "; ".join(issues) + ".",
                location_id=receiving.location_id,
            ),
            now=now,
        )
    db.flush()
    return receiving
