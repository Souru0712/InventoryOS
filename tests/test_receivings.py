"""T2.11-a: receivings on the ledger (RCV-FR-01, -05..07, -09..12, AC-RCV-01, -05..08, AC-PRC-02)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.items import NewItem, create_item
from app.ledger.posting import balance
from app.models import ExceptionRecord, InventoryMovement, StockBatch, StockItem
from app.receivings import (
    AlreadyPosted,
    add_line,
    correct_line,
    post_receiving,
    start_receiving,
)
from app.suppliers import SupplierFields, add_supplier_item, create_supplier
from app.transform.receivings import ReceivingRuleError

D = Decimal


@pytest.fixture
def dock(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))

    def item(name, dimension="count", unit="each"):
        return create_item(
            db,
            org,
            NewItem(name=name, location_ids=[location.id], dimension=dimension, display_unit=unit),
        )

    db.commit()
    return org, item


def _receipts(db, item_id):
    return db.scalars(
        select(InventoryMovement).where(
            InventoryMovement.inventory_item_id == item_id,
            InventoryMovement.movement_type.in_(["receipt", "reversal"]),
        )
    ).all()


def _cost(db, location, item):
    stock = db.scalars(select(StockItem).where(StockItem.inventory_item_id == item.id)).one()
    return stock.cost_per_base_cents


def test_ac_rcv_01_a_manual_delivery_of_three_lines_posts_three_receipts(db, dock, location):
    org, item = dock
    things = [item(n) for n in ("Cups", "Lids", "Straws")]
    delivery = start_receiving(db, org, location_id=location.id)
    for thing, price in zip(things, ("0.10", "0.05", "0.02"), strict=True):
        add_line(db, org, delivery.id, inventory_item_id=thing.id, accepted="100", unit_price=price)
    post_receiving(db, org, delivery.id)
    db.commit()
    for thing, cents in zip(things, (D(10), D(5), D(2)), strict=True):
        [receipt] = _receipts(db, thing.id)
        assert (receipt.qty_base, receipt.unit_cost_snapshot) == (D(100), cents)
        assert _cost(db, location, thing) == cents
        assert balance(db, org, location.id, thing.id) == D(100)


def test_ac_rcv_05_a_delivery_posts_once_and_the_second_is_already_posted(db, dock, location):
    org, item = dock
    cups = item("Cups")
    delivery = start_receiving(db, org, location_id=location.id)
    add_line(db, org, delivery.id, inventory_item_id=cups.id, accepted="10")
    post_receiving(db, org, delivery.id)
    db.commit()
    with pytest.raises(AlreadyPosted, match="already posted"):
        post_receiving(db, org, delivery.id)
    db.rollback()
    assert len(_receipts(db, cups.id)) == 1
    with pytest.raises(AlreadyPosted):
        add_line(db, org, delivery.id, inventory_item_id=cups.id, accepted="1")


def test_ac_rcv_06_short_and_rejected_are_recorded_and_only_the_accepted_posts(db, dock, location):
    org, item = dock
    eggs = item("Eggs")
    delivery = start_receiving(db, org, location_id=location.id)
    with pytest.raises(ReceivingRuleError, match="Say why"):
        add_line(
            db,
            org,
            delivery.id,
            inventory_item_id=eggs.id,
            invoiced="10",
            arrived="9",
            accepted="8",
        )
    with pytest.raises(ReceivingRuleError, match="More cannot be accepted"):
        add_line(db, org, delivery.id, inventory_item_id=eggs.id, arrived="5", accepted="6")
    add_line(
        db,
        org,
        delivery.id,
        inventory_item_id=eggs.id,
        invoiced="10",
        arrived="9",
        accepted="8",
        reject_reason="cracked",
    )
    post_receiving(db, org, delivery.id)
    db.commit()
    assert balance(db, org, location.id, eggs.id) == D(8)
    flagged = db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.type == "receiving_discrepancy")
    ).one()
    assert (
        "short 1" in flagged.title and "rejected 1" in flagged.title and "cracked" in flagged.title
    )


def test_ac_rcv_07_and_prc_02_supplier_packs_convert_and_a_price_jump_is_flagged(
    db, dock, location
):
    org, item = dock
    milk = item("Milk", "volume", "l")
    farm = create_supplier(db, org, SupplierFields(name="Valley Farms"))
    case = add_supplier_item(
        db,
        org,
        farm.id,
        inventory_item_id=milk.id,
        purchase_unit="case",
        pack_quantity="6",
        price="10.00",
    )
    delivery = start_receiving(db, org, location_id=location.id, supplier_id=farm.id)
    line = add_line(
        db,
        org,
        delivery.id,
        inventory_item_id=milk.id,
        supplier_item_id=case.id,
        accepted="2",
        unit_price="12.00",
    )
    assert (line.unit, line.qty_base) == ("case", D(12000))
    post_receiving(db, org, delivery.id)
    db.commit()
    assert line.price_flag and case.last_price_cents == 1200
    assert (
        db.scalars(select(ExceptionRecord).where(ExceptionRecord.type == "price_variance"))
        .one()
        .status
        == "open"
    )
    assert _cost(db, location, milk) == D("0.2")  # 1200 cents over 6000 ml


def test_ac_rcv_08_a_correction_reverses_the_line_and_posts_the_right_one(db, dock, location):
    org, item = dock
    cups = item("Cups")
    delivery = start_receiving(db, org, location_id=location.id)
    first = add_line(
        db, org, delivery.id, inventory_item_id=cups.id, accepted="10", unit_price="0.10"
    )
    post_receiving(db, org, delivery.id)
    fixed = correct_line(db, org, first.id, accepted="8", reason="Counted again: 8 boxes")
    db.commit()
    assert (first.status, fixed.status, fixed.corrects_line_id) == ("corrected", "posted", first.id)
    moves = sorted((m.movement_type, m.qty_base) for m in _receipts(db, cups.id))
    assert moves == [("receipt", D(8)), ("receipt", D(10)), ("reversal", D(-10))]
    assert balance(db, org, location.id, cups.id) == D(8)
    with pytest.raises(ReceivingRuleError, match="Only a posted line"):
        correct_line(db, org, first.id, accepted="7", reason="again")


def test_a_dated_line_receives_into_its_own_batch(db, dock, location):
    org, item = dock
    yoghurt = item("Yoghurt")
    delivery = start_receiving(db, org, location_id=location.id)
    line = add_line(
        db,
        org,
        delivery.id,
        inventory_item_id=yoghurt.id,
        accepted="12",
        expires_on=date.today() + timedelta(days=5),
        lot_code="L-22",
    )
    post_receiving(db, org, delivery.id)
    batch = db.get(StockBatch, line.batch_id)
    assert (batch.received_qty_base, batch.lot_code) == (D(12), "L-22")


def test_receiving_through_the_pages(db, dock, location):
    org, item = dock
    cups = item("Cups")
    db.commit()
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        started = client.post("/app/deliveries", data={"location_id": location.id})
        assert "Draft" in started.text
        delivery = started.url.path.rsplit("/", 1)[-1]
        added = client.post(
            f"/app/deliveries/{delivery}/lines",
            data={"inventory_item_id": cups.id, "accepted": "24"},
        )
        assert "Line added." in added.text
        posted = client.post(f"/app/deliveries/{delivery}/post")
        assert "Posted." in posted.text
    db.expire_all()
    assert balance(db, org, location.id, cups.id) == D(24)


# --- T2.11-b: invoices become receivings; the reading cap ----------------------------------------


def _invoice(db, org, location, stock, supplier, qty_base, total_cents):
    from app.models import Document, DocumentLine, DocumentStatus, LineStatus

    document = Document(
        tenant_id=org.tenant_id,
        location_id=location.id,
        supplier=supplier,
        storage_key="k",
        sha256=str(qty_base).rjust(64, "c"),
        status=DocumentStatus.PARSED,
    )
    db.add(document)
    db.flush()
    line = DocumentLine(
        tenant_id=org.tenant_id,
        document_id=document.id,
        line_no=1,
        raw_text="milk",
        parsed_name="Milk 6x1L",
        parsed_qty=D(1),
        line_total_cents=total_cents,
        stock_item_id=stock.id,
        received_qty_base=qty_base,
        status=LineStatus.MATCHED,
    )
    db.add(line)
    db.flush()
    return document, line


def test_an_applied_invoice_is_a_posted_receiving_with_its_price_checked(db, dock, location):
    from app.models import Receiving
    from app.orchestration.receiving import LineDecision, apply_document

    org, item = dock
    milk = item("Milk", "volume", "l")
    stock = db.scalars(select(StockItem).where(StockItem.inventory_item_id == milk.id)).one()
    farm = create_supplier(db, org, SupplierFields(name="Valley Farms"))
    add_supplier_item(
        db,
        org,
        farm.id,
        inventory_item_id=milk.id,
        purchase_unit="case",
        pack_quantity="6",
        price="10.00",
    )
    document, line = _invoice(db, org, location, stock, "valley farms", D(6000), 1300)
    apply_document(db, org, document.id, [LineDecision(line.id, stock.id, D(6000))])
    db.commit()
    receiving = db.scalars(select(Receiving).where(Receiving.document_id == document.id)).one()
    assert (receiving.status, receiving.supplier_id) == ("posted", farm.id)
    [received] = receiving.lines
    assert received.qty_base == D(6000) and received.transaction_id is not None
    assert received.price_flag  # 13.00 a case against 10.00
    assert balance(db, org, location.id, milk.id) == D(6000)  # posted once, by the invoice


def test_a_cost_changed_without_cost_rights_is_flagged(db, dock, tenant, location):
    from app.models import Membership, User
    from app.orchestration.receiving import LineDecision, apply_document
    from app.security import hash_password

    org, item = dock
    cups = item("Cups")
    stock = db.scalars(select(StockItem).where(StockItem.inventory_item_id == cups.id)).one()
    user = User(
        tenant_id=tenant.id, email="emp@rosewood.test", password_hash=hash_password("x" * 12)
    )
    db.add(user)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role="employee"))
    staff = OrgContext(
        tenant_id=tenant.id, user_id=user.id, permissions=role_permissions("employee")
    )
    document, line = _invoice(db, org, location, stock, "Corner Shop", D(100), 500)
    apply_document(
        db, staff, document.id, [LineDecision(line.id, stock.id, D(100), line_total_cents=200)]
    )
    db.commit()
    flagged = db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.type == "receiving_discrepancy")
    ).one()
    assert "without cost rights" in flagged.title


def test_reading_invoices_stops_at_the_daily_cap(db, dock, location, monkeypatch):
    from app.config import get_settings
    from app.models import Document, DocumentStatus
    from app.web.receiving import read_later

    org, _item = dock
    monkeypatch.setattr(get_settings(), "extraction_daily_cap", 1)
    first, second = (
        Document(
            tenant_id=org.tenant_id,
            location_id=location.id,
            storage_key=f"k{n}",
            sha256=str(n) * 64,
            status=DocumentStatus.UPLOADED,
        )
        for n in (1, 2)
    )
    db.add_all([first, second])
    db.flush()
    assert read_later(db, org, first) is True
    db.flush()
    assert read_later(db, org, second) is False
    assert second.status is DocumentStatus.NEEDS_REVIEW and "by hand" in second.error
