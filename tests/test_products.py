"""T2.01: the owned product catalog (RCP-FR-01, -03, C-36, DM-9)."""

import argparse
import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.access import OrgContext
from app.cli import cmd_demo
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    AuditEvent,
    Membership,
    Modifier,
    Product,
    ProductVariation,
    ProviderConnection,
    SoldItem,
    User,
)
from app.products import (
    NewVariation,
    create_product,
    list_products,
    products_from_sold_items,
    set_product_active,
    update_variation,
)
from app.security import hash_password
from app.transform.products import (
    ProductRuleError,
    check_modifier,
    check_price,
    plan_from_sold,
    split_sold_name,
)

PASSWORD = "a-good-password"  # noqa: S105 (the test accounts', as in conftest)
MONEY = re.compile(r"\$\s?\d")


def member(db, tenant, role):
    user = User(
        tenant_id=tenant.id, email=f"{role}@rosewood.test", password_hash=hash_password(PASSWORD)
    )
    db.add(user)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
    db.commit()


def signed_in(email="owner@rosewood.test") -> TestClient:
    client = TestClient(app)
    client.__enter__()
    client.post("/login", data={"email": email, "password": PASSWORD})
    return client


# --- the rules (pure) --------------------------------------------------------------------------


def test_a_price_is_an_amount_in_cents_and_blank_is_none():
    assert check_price("6.50") == 650
    assert check_price("$7") == 700
    assert check_price(" ") is None
    for bad in ("6.505", "-1", "four", "NaN"):
        with pytest.raises(ProductRuleError, match="like 4.50"):
            check_price(bad)


def test_only_a_scaling_modifier_keeps_a_factor_and_it_is_above_zero():
    assert check_modifier("scale", "1.15") == Decimal("1.15")
    assert check_modifier("none", "1.15") is None
    assert check_modifier("add", "") is None
    for effect, factor in (("scale", ""), ("scale", "0"), ("scale", "-1"), ("remove", "")):
        with pytest.raises(ProductRuleError):
            check_modifier(effect, factor)


def test_a_pos_sale_name_splits_into_product_and_size():
    assert split_sold_name("Golden State - 16 oz") == ("Golden State", "16 oz")
    assert split_sold_name("Bagel - Everything - Large") == ("Bagel - Everything", "Large")
    assert split_sold_name("  Croissant ") == ("Croissant", "Regular")
    plans = plan_from_sold(
        [
            (1, "Latte - 12 oz", "Drinks", 425, True),
            (2, "latte - 16 oz", "Drinks", 495, False),
            (3, "Croissant", "Food", 0, True),
            (4, "Croissant - Regular", "Food", 300, True),  # the same size twice
            (5, "Muffin", "Food", 300, True),  # already in the catalog
        ],
        taken=["MUFFIN"],
    )
    assert [(p.name, p.active, [v[1:] for v in p.variations]) for p in plans] == [
        ("Latte", True, [("12 oz", 425, True), ("16 oz", 495, False)]),
        ("Croissant", True, [("Regular", None, True), ("Regular 2", 300, True)]),
    ]


# --- the acceptance: a Manager builds it with no POS -------------------------------------------


def test_a_manager_creates_golden_state_in_two_sizes_and_light_ice_with_no_pos(
    db, tenant, location
):
    member(db, tenant, "manager")
    assert db.scalar(select(func.count()).select_from(ProviderConnection)) == 0
    client = signed_in("manager@rosewood.test")
    response = client.post(
        "/app/products/new",
        data={
            "name": "Golden State",
            "category": "Juices",
            "variation_name": ["12 oz", "16 oz", "", ""],
            "variation_price": ["6.50", "7.50", "", ""],
        },
    )
    assert response.status_code == 200 and "Golden State added." in response.text
    added = client.post(
        "/app/modifiers",
        data={"name": "Light ice", "group": "Ice", "effect": "scale", "scale_factor": "1.15"},
    )
    assert "Modifier added." in added.text

    db.expire_all()
    product = db.scalars(select(Product)).one()
    assert (product.name, product.category) == ("Golden State", "Juices")
    assert [(v.name, v.price_cents) for v in product.variations] == [
        ("12 oz", 650),
        ("16 oz", 750),
    ]
    ice = db.scalars(select(Modifier)).one()
    assert (ice.name, ice.group_name, ice.effect, ice.scale_factor) == (
        "Light ice",
        "Ice",
        "scale",
        Decimal("1.15"),
    )
    page = client.get("/app/products").text
    assert "Golden State" in page and "12 oz $6.50" in page and "16 oz $7.50" in page
    assert "Light ice" in page
    actions = sorted(db.scalars(select(AuditEvent.action)))
    assert actions == ["product.created", "product.modifier_created"]


def test_a_mistake_on_the_form_is_explained_and_keeps_what_was_typed(db, tenant, location):
    page = signed_in().post(
        "/app/products/new",
        data={"name": "Golden State", "variation_name": ["12 oz"], "variation_price": ["6.5.0"]},
    )
    assert page.status_code == 400
    assert "A price is an amount like 4.50." in page.text and 'value="Golden State"' in page.text
    assert db.scalar(select(func.count()).select_from(Product)) == 0


# --- the catalog's rules -----------------------------------------------------------------------


def test_names_are_unique_and_a_product_keeps_one_size_on_sale(db, tenant, location):
    org = OrgContext.system(tenant.id)
    juice = create_product(
        db, org, name="Golden State", variations=[NewVariation("12 oz"), NewVariation("16 oz")]
    )
    with pytest.raises(ProductRuleError, match="already a product called golden state"):
        create_product(db, org, name="golden state", variations=[])
    with pytest.raises(ProductRuleError, match="listed twice"):
        create_product(db, org, name="Mango", variations=[NewVariation("S"), NewVariation("s")])
    small, large = juice.variations
    update_variation(db, org, juice.id, small.id, name="12 oz", price=None, active=False)
    with pytest.raises(ProductRuleError, match="needs one size on sale"):
        update_variation(db, org, juice.id, large.id, name="16 oz", price="", active=False)
    plain = create_product(db, org, name="Water", variations=[])
    assert [v.name for v in plain.variations] == ["Regular"]  # sold one way


def test_a_deactivated_product_leaves_the_list_and_nothing_is_deleted(db, tenant, location):
    org = OrgContext.system(tenant.id)
    juice = create_product(db, org, name="Golden State", variations=[])
    set_product_active(db, org, juice.id, False)
    db.commit()
    assert list_products(db, org) == []
    assert [p.name for p in list_products(db, org, inactive=True)] == ["Golden State"]
    page = signed_in().get("/app/products?show=all").text
    assert "Golden State" in page and "inactive" in page


def test_an_employee_reads_the_catalog_without_prices_and_cannot_change_it(db, tenant, location):
    org = OrgContext.system(tenant.id)
    juice = create_product(db, org, name="Golden State", variations=[NewVariation("12 oz", "6.50")])
    db.commit()
    member(db, tenant, "employee")
    client = signed_in("employee@rosewood.test")
    for path in ("/app/products", f"/app/products/{juice.id}"):
        page = client.get(path)
        assert page.status_code == 200 and "Golden State" in page.text
        assert not MONEY.search(page.text) and "6.50" not in page.text
        assert "Add a product" not in page.text and 'name="price"' not in page.text
    assert client.post("/app/products/new", data={"name": "Mango"}).status_code == 403
    assert client.post("/app/modifiers", data={"name": "Oat milk"}).status_code == 403


# --- DM-9: the POS sale items become owned products --------------------------------------------


def test_sale_items_become_products_once_each(db, tenant, location):
    for name, price in (("Latte - 12 oz", 425), ("Latte - 16 oz", 495), ("Croissant", 0)):
        db.add(
            SoldItem(
                tenant_id=tenant.id,
                provider_item_id=name,
                name=name,
                category="Menu",
                price_cents=price,
            )
        )
    db.commit()
    org = OrgContext.system(tenant.id)
    assert products_from_sold_items(db, org) == 3
    assert products_from_sold_items(db, org) == 0  # again: nothing more
    shown = {
        p.name: [(v.name, v.price_cents, v.source_sold_item_id is not None) for v in p.variations]
        for p in list_products(db, org)
    }
    assert shown == {
        "Croissant": [("Regular", None, True)],
        "Latte": [("12 oz", 425, True), ("16 oz", 495, True)],
    }


def test_the_demo_cafe_owns_a_product_for_each_thing_it_sells():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    args = argparse.Namespace(
        email="demo@stockpulse.local", password="demopassword", name="Rosewood Coffee", days=1
    )
    assert cmd_demo(args) == 0
    with SessionLocal() as db:
        sold = db.scalar(select(func.count()).select_from(SoldItem))
        linked = db.scalar(
            select(func.count())
            .select_from(ProductVariation)
            .where(ProductVariation.source_sold_item_id.is_not(None))
        )
        assert sold and linked == sold
