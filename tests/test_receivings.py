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
