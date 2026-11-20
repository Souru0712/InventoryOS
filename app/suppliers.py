"""Suppliers and supplier items (T2.10; PRC-FR-01..03, -08, DM-11, ADR-0061).

The only writer of `suppliers` and `supplier_items`. A supplier is deactivated, never deleted,
so its deliveries and history stay (AC-PRC-01). An item may come from several suppliers, each
in its own purchase unit and pack (AC-PRC-02); one of them is preferred. A new price far from
the last one opens `price_variance` (PRC-FR-08). Purchase history reads the deliveries received
from the supplier (PRC-FR-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.exceptions import record_exception
from app.items import entered_base
from app.models import Document, DocumentStatus, InventoryItem, Supplier, SupplierItem
from app.transform.business_days import open_days_mask
from app.transform.exceptions import Finding
from app.transform.items import ItemRuleError
from app.transform.suppliers import (
    ORDERING_METHODS,
    SupplierRuleError,
    check_lead_time,
    check_pack,
    clean_name,
    price_change,
)


class NotFound(LookupError):
    """A supplier or supplier item outside the business, or not there at all."""


@dataclass(frozen=True)
class SupplierFields:
    name: str
    contact_name: str = ""
    phone: str = ""
    email: str = ""
    ordering_method: str = "email"
    delivery_days: tuple[str, ...] = ()
    lead_time_days: str = "2"
    minimum_order: str = ""
    notes: str = ""


def list_suppliers(db: Session, org: OrgContext, *, inactive: bool = False) -> list[Supplier]:
    rows = db.scalars(select_scoped(org, Supplier).order_by(func.lower(Supplier.name)))
    return [s for s in rows if inactive or s.is_active]


def get_supplier(db: Session, org: OrgContext, supplier_id: int) -> Supplier:
    supplier = get_scoped(db, org, Supplier, supplier_id)
    if supplier is None:
        raise NotFound(supplier_id)
    return supplier


def _money(raw: str) -> int | None:
    raw = raw.strip().lstrip("$")
    if not raw:
        return None
    try:
        cents = int((Decimal(raw) * 100).quantize(Decimal(1)))
    except InvalidOperation as exc:
        raise SupplierRuleError(f"{raw} is not an amount.") from exc
    if cents < 0:
        raise SupplierRuleError("An amount cannot be below zero.")
    return cents


def _apply(db: Session, org: OrgContext, row: Supplier, fields: SupplierFields) -> None:
    name = clean_name(fields.name)
    if any(
        s.id != row.id and s.name.casefold() == name.casefold()
        for s in list_suppliers(db, org, inactive=True)
    ):
        raise SupplierRuleError(f"There is already a supplier called {name}.")
    if fields.ordering_method not in ORDERING_METHODS:
        raise SupplierRuleError("Choose how orders are placed.")
    try:
        lead = check_lead_time(int(fields.lead_time_days or "0"))
        days = open_days_mask(fields.delivery_days)
    except ValueError as exc:
        raise SupplierRuleError(
            str(exc) if isinstance(exc, SupplierRuleError) else "Check the lead time and days."
        ) from exc
    row.name = name
    row.contact_name = fields.contact_name.strip()[:120] or None
    row.phone = fields.phone.strip()[:40] or None
    row.email = fields.email.strip()[:255] or None
    row.ordering_method = fields.ordering_method
    row.delivery_days = days
    row.lead_time_days = lead
    row.minimum_order_cents = _money(fields.minimum_order)
    row.notes = fields.notes.strip() or None


def create_supplier(db: Session, org: OrgContext, fields: SupplierFields) -> Supplier:
    row = Supplier(tenant_id=org.tenant_id, name="")
    _apply(db, org, row, fields)
    db.add(row)
    db.flush()
    return row


def update_supplier(
    db: Session, org: OrgContext, supplier_id: int, fields: SupplierFields
) -> Supplier:
    row = get_supplier(db, org, supplier_id)
    _apply(db, org, row, fields)
    db.flush()
    return row


def set_supplier_active(db: Session, org: OrgContext, supplier_id: int, active: bool) -> Supplier:
    """Deactivate or bring back; nothing it delivered goes anywhere (AC-PRC-01)."""
    row = get_supplier(db, org, supplier_id)
    row.is_active = active
    db.flush()
    return row


def _pack_base(item: InventoryItem, quantity: str, unit: str) -> Decimal:
    try:
        return check_pack(entered_base(item, quantity or "1", unit))
    except ItemRuleError as exc:
        raise SupplierRuleError(str(exc)) from exc


def _prefer(db: Session, org: OrgContext, row: SupplierItem) -> None:
    for other in db.scalars(
        select_scoped(org, SupplierItem).where(
            SupplierItem.inventory_item_id == row.inventory_item_id, SupplierItem.id != row.id
        )
    ):
        other.preferred = False
    row.preferred = True


def add_supplier_item(
    db: Session,
    org: OrgContext,
    supplier_id: int,
    *,
    inventory_item_id: int,
    purchase_unit: str,
    pack_quantity: str,
    pack_unit: str = "",
    code: str = "",
    name: str = "",
    price: str = "",
    preferred: bool = False,
) -> SupplierItem:
    """How a supplier sells an item: e.g. a "case" that holds 6 L (PRC-FR-02)."""
    supplier = get_supplier(db, org, supplier_id)
    item = get_scoped(db, org, InventoryItem, inventory_item_id)
    if item is None:
        raise NotFound(inventory_item_id)
    row = SupplierItem(
        tenant_id=org.tenant_id,
        supplier_id=supplier.id,
        inventory_item_id=item.id,
        code=code.strip()[:80] or None,
        name=name.strip()[:300] or None,
        purchase_unit=purchase_unit.strip()[:40] or item.display_unit,
        pack_size_base=_pack_base(item, pack_quantity, pack_unit),
    )
    db.add(row)
    db.flush()
    cents = _money(price)
    if cents is not None:
        row.last_price_cents = cents
        row.last_price_at = datetime.now(UTC)
    has_preferred = db.scalars(
        select_scoped(org, SupplierItem).where(
            SupplierItem.inventory_item_id == item.id,
            SupplierItem.preferred.is_(True),
            SupplierItem.id != row.id,
        )
    ).first()
    if preferred or has_preferred is None:
        _prefer(db, org, row)
    db.flush()
    return row


def get_supplier_item(db: Session, org: OrgContext, supplier_item_id: int) -> SupplierItem:
    row = get_scoped(db, org, SupplierItem, supplier_item_id)
    if row is None:
        raise NotFound(supplier_item_id)
    return row


def record_price(
    db: Session,
    org: OrgContext,
    supplier_item_id: int,
    price_cents: int,
    *,
    at: datetime | None = None,
) -> SupplierItem:
    """A new price per purchase unit (from a delivery or by hand); far from the last one, it
    opens `price_variance` on that supplier item (PRC-FR-08)."""
    row = get_supplier_item(db, org, supplier_item_id)
    change = price_change(row.last_price_cents, price_cents)
    if change is not None and change.notable:
        item = get_scoped(db, org, InventoryItem, row.inventory_item_id)
        record_exception(
            db,
            org,
            Finding(
                type="price_variance",
                subject_type="supplier_item",
                subject_id=str(row.id),
                title=(
                    f"{row.supplier.name} now charges {change.percent:+}% for "
                    f"{item.name if item else 'an item'} ({row.purchase_unit})."
                ),
                money_impact_cents=change.after_cents - change.before_cents,
            ),
        )
    row.last_price_cents = price_cents
    row.last_price_at = at or datetime.now(UTC)
    db.flush()
    return row


def update_supplier_item(
    db: Session,
    org: OrgContext,
    supplier_item_id: int,
    *,
    purchase_unit: str,
    pack_quantity: str,
    pack_unit: str = "",
    code: str = "",
    name: str = "",
    price: str = "",
    preferred: bool = False,
    active: bool = True,
) -> SupplierItem:
    row = get_supplier_item(db, org, supplier_item_id)
    item = get_scoped(db, org, InventoryItem, row.inventory_item_id)
    if item is None:
        raise NotFound(supplier_item_id)
    row.purchase_unit = purchase_unit.strip()[:40] or row.purchase_unit
    row.pack_size_base = _pack_base(item, pack_quantity, pack_unit)
    row.code = code.strip()[:80] or None
    row.name = name.strip()[:300] or None
    row.is_active = active
    if preferred:
        _prefer(db, org, row)
    cents = _money(price)
    if cents is not None and cents != row.last_price_cents:
        record_price(db, org, row.id, cents)
    db.flush()
    return row


def supplier_items_of(db: Session, org: OrgContext, inventory_item_id: int) -> list[SupplierItem]:
    """Everyone an item comes from, preferred first (AC-PRC-02)."""
    rows = db.scalars(
        select_scoped(org, SupplierItem).where(
            SupplierItem.inventory_item_id == inventory_item_id, SupplierItem.is_active.is_(True)
        )
    )
    return sorted(rows, key=lambda r: (not r.preferred, r.id))


def purchases(db: Session, org: OrgContext, supplier: Supplier, limit: int = 50) -> list[Document]:
    """Deliveries received from the supplier, newest first (PRC-FR-03)."""
    rows = db.scalars(
        select_scoped(org, Document)
        .where(
            func.lower(func.trim(Document.supplier)) == supplier.name.casefold(),
            Document.status == DocumentStatus.APPLIED,
        )
        .order_by(Document.uploaded_at.desc())
        .limit(limit)
    )
    return list(rows)
