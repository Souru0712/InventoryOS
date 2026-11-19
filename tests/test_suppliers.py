"""T2.10: suppliers and supplier items (PRC-FR-01..03, -08, AC-PRC-01, -02, DM-11)."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.items import NewItem, create_item
from app.models import Document, DocumentStatus, ExceptionRecord, Supplier
from app.suppliers import (
    SupplierFields,
    add_supplier_item,
    create_supplier,
    list_suppliers,
    purchases,
    record_price,
    set_supplier_active,
    supplier_items_of,
)
from app.transform.business_days import open_day_names
from app.transform.suppliers import SupplierRuleError, in_base

D = Decimal


@pytest.fixture
def buying(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    milk = create_item(
        db,
        org,
        NewItem(name="Milk", location_ids=[location.id], dimension="volume", display_unit="l"),
    )
    db.commit()
    return org, milk


def test_ac_prc_01_a_supplier_with_lead_time_and_days_and_deactivation_keeps_history(
    db, buying, tenant, location
):
    org, milk = buying
    farm = create_supplier(
        db,
        org,
        SupplierFields(name="Valley Farms", lead_time_days="3", delivery_days=("mon", "thu")),
    )
    add_supplier_item(
        db, org, farm.id, inventory_item_id=milk.id, purchase_unit="case", pack_quantity="6"
    )
    db.add(
        Document(
            tenant_id=tenant.id,
            location_id=location.id,
            supplier=" valley farms ",
            storage_key="k",
            sha256="a" * 64,
            status=DocumentStatus.APPLIED,
        )
    )
    db.commit()
    assert (farm.lead_time_days, open_day_names(farm.delivery_days)) == (3, ("mon", "thu"))
    set_supplier_active(db, org, farm.id, False)
    db.commit()
    assert list_suppliers(db, org) == []
    assert [s.name for s in list_suppliers(db, org, inactive=True)] == ["Valley Farms"]
    assert len(farm.items) == 1 and len(purchases(db, org, farm)) == 1  # history stays
    with pytest.raises(SupplierRuleError, match="already a supplier"):
        create_supplier(db, org, SupplierFields(name="valley farms"))
    with pytest.raises(SupplierRuleError, match="Lead time"):
        create_supplier(db, org, SupplierFields(name="Hill Co", lead_time_days="400"))


def test_ac_prc_02_two_suppliers_two_packs_convert_to_the_same_base(db, buying):
    org, milk = buying
    farm = create_supplier(db, org, SupplierFields(name="Valley Farms"))
    shop = create_supplier(db, org, SupplierFields(name="Corner Shop"))
    case = add_supplier_item(
        db,
        org,
        farm.id,
        inventory_item_id=milk.id,
        purchase_unit="case",
        pack_quantity="6",
        price="12.00",
    )
    bottle = add_supplier_item(
        db,
        org,
        shop.id,
        inventory_item_id=milk.id,
        purchase_unit="bottle",
        pack_quantity="1000",
        pack_unit="ml",
        price="2.40",
        preferred=True,
    )
    assert (case.pack_size_base, bottle.pack_size_base) == (D(6000), D(1000))
    assert in_base(D(2), case.pack_size_base) == in_base(D(12), bottle.pack_size_base) == D(12000)
    assert (case.last_price_cents, bottle.last_price_cents) == (1200, 240)
    assert [s.id for s in supplier_items_of(db, org, milk.id)] == [bottle.id, case.id]
    assert not case.preferred  # one preferred supplier per item


def test_prc_fr_08_a_big_price_change_is_flagged_and_a_small_one_is_not(db, buying):
    org, milk = buying
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
    record_price(db, org, case.id, 1050)  # +5%
    db.commit()
    assert db.scalars(select(ExceptionRecord)).all() == []
    record_price(db, org, case.id, 1260)  # +20%
    db.commit()
    flagged = db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.type == "price_variance")
    ).one()
    assert "+20.0%" in flagged.title and flagged.money_impact_cents == 210


def test_the_supplier_pages(db, buying):
    org, milk = buying
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        made = client.post(
            "/app/suppliers",
            data={"name": "Valley Farms", "lead_time_days": "2", "delivery_days": ["tue", "fri"]},
        )
        assert "Valley Farms added." in made.text
        farm = db.scalars(select(Supplier)).one()
        added = client.post(
            f"/app/suppliers/{farm.id}/items",
            data={"inventory_item_id": milk.id, "purchase_unit": "case", "pack_quantity": "6"},
        )
        assert "Item added." in added.text and "1 case = 6" in added.text
        assert "Suppliers" in client.get("/app/suppliers").text
