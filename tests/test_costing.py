"""T2.03: recipe costing, margins, completeness (RCP-FR-10..13, -17, AC-RCP-03, -07, -08)."""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.costing import cost_changes_from, costing_location, impact, sold_without_recipe
from app.costs import set_cost
from app.items import NewItem, create_item
from app.main import app
from app.models import (
    Document,
    DocumentStatus,
    Membership,
    SoldItem,
    StockItem,
    TrackMode,
    User,
)
from app.products import NewVariation, create_product, products_from_sold_items
from app.recipes import NewLine, Owner, save_recipe
from app.security import hash_password
from app.transform.business_days import business_date
from app.transform.costing import cost_recipe, margin_of, per_base_cost
from app.transform.recipes import Line
from app.units import Dimension
from tests.sales_helpers import sold_on

D = Decimal
PASSWORD = "a-good-password"  # noqa: S105 (the test accounts', as in conftest)
MONEY = re.compile(r"\$\s?\d")


@pytest.fixture
def kitchen(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))

    def item(name, dimension, unit, cost, item_type="raw"):
        return create_item(
            db,
            org,
            NewItem(
                name=name,
                location_ids=[location.id],
                dimension=dimension,
                display_unit=unit,
                item_type=item_type,
                cost_per_unit=cost,
            ),
        )

    made = {
        "milk": item("Milk", "volume", "l", "2.00"),  # 0.2 cents a ml
        "beans": item("Espresso beans", "weight", "kg", "30.00"),  # 3 cents a g
        "cup": item("Cup 16 oz", "count", "each", "0.10", "packaging"),
        "ginger": item("Ginger", "weight", "kg", "10.00"),  # 1 cent a g
        "juice": item("Ginger juice", "volume", "l", "0", "prepared"),  # no cost of its own
    }
    golden = create_product(
        db,
        org,
        name="Golden State",
        variations=[NewVariation("12 oz", "6.50"), NewVariation("16 oz", "7.50")],
    )
    db.commit()
    return org, made, golden


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


def recipe(db, org, owner, *lines, **extra):
    version = save_recipe(db, org, owner, [NewLine(i.id, q, u) for i, q, u in lines], **extra)
    db.commit()
    return version


# --- the rules (pure) --------------------------------------------------------------------------


def test_a_recipe_costs_its_lines_split_into_ingredients_and_packaging():
    lines = [
        Line(1, D(250), "ml", D(250)),
        Line(2, D(18), "g", D(18)),
        Line(3, D(1), "each", D(1), is_packaging=True),
        Line(4, D(60), "ml", D(60)),
    ]
    cost = cost_recipe(lines, {1: D("0.2"), 2: D(3), 3: D(10), 4: None}, D(1))
    assert (cost.ingredient_cents, cost.packaging_cents, cost.total_cents) == (
        D(104),
        D(10),
        D(114),
    )
    assert cost.missing == [4] and not cost.complete  # listed, not priced at zero
    halves = cost_recipe(lines[:3], {1: D("0.2"), 2: D(3), 3: D(10)}, D(2))
    assert halves.per_yield_cents == D(57)  # two servings
    margin = margin_of(750, halves)
    assert (margin.amount_cents, margin.percent, margin.label) == (
        D(693),
        D("92.4"),
        "estimated at today's costs",
    )
    assert not margin_of(750, cost).known  # a gap: no margin claimed
    assert margin_of(None, halves).amount_cents is None
    assert per_base_cost(cost) is None  # an unknown input makes the made item unknown


# --- AC-RCP-03: the cost panel -----------------------------------------------------------------


def test_ac_rcp_03_the_card_shows_line_costs_the_gaps_and_an_estimated_margin(db, tenant, kitchen):
    org, item, golden = kitchen
    large = golden.variations[1]
    recipe(
        db,
        org,
        Owner("variation", large.id),
        (item["milk"], "250", "ml"),
        (item["beans"], "18", "g"),
        (item["cup"], "1", ""),
        (item["juice"], "60", "ml"),
    )
    card = signed_in().get(f"/app/recipes/variation/{large.id}").text
    assert "Ingredients $1.04" in card and "packaging $0.10" in card
    assert "$1.14 a serving" in card and "(at least)" in card
    assert "No cost yet for Ginger juice" in card
    assert "the margin is unknown until every ingredient has a cost" in card

    recipe(  # the juice is made in house: its production recipe gives it a cost
        db,
        org,
        Owner("item", item["juice"].id),
        (item["ginger"], "450", "g"),
        yield_quantity="0.5",
        yield_unit="l",
    )
    card = signed_in().get(f"/app/recipes/variation/{large.id}").text
    assert "$1.68 a serving" in card  # 114 + 60 ml at 0.9 cents
    assert "margin $5.82 (77.6%)</strong>, estimated at today&#39;s costs" in card
    product = signed_in().get(f"/app/products/{golden.id}").text
    assert "costs $1.68" in product and "margin $5.82 (77.6%)" in product

    member(db, tenant, "employee")
    employee = signed_in("employee@rosewood.test")
    for path in (f"/app/recipes/variation/{large.id}", f"/app/products/{golden.id}"):
        page = employee.get(path).text
        assert "Milk" in page or "Golden State" in page
        assert not MONEY.search(page) and "estimated at today" not in page  # AC-RCP-09 holds
        assert "a serving" not in page and "No cost yet" not in page


# --- AC-RCP-07: a receipt that raises a cost ---------------------------------------------------


def test_ac_rcp_07_a_receipt_raising_a_cost_shows_the_recipes_and_margins_it_moves(
    db, tenant, location, kitchen
):
    org, item, golden = kitchen
    small, large = golden.variations
    recipe(db, org, Owner("variation", small.id), (item["milk"], "200", "ml"))
    recipe(db, org, Owner("variation", large.id), (item["milk"], "250", "ml"))
    recipe(db, org, Owner("variation", large.id), (item["beans"], "18", "g"))  # no milk now
    document = Document(
        tenant_id=tenant.id,
        location_id=location.id,
        storage_key="k",
        sha256="0" * 64,
        status=DocumentStatus.APPLIED,
    )
    db.add(document)
    db.flush()
    set_cost(
        db,
        OrgContext.system(tenant.id),
        location_id=location.id,
        inventory_item_id=item["milk"].id,
        cost=D("0.5"),  # 0.2 to 0.5 cents a ml
        source_type="receipt",
        source_id=str(document.id),
    )
    db.commit()

    [change] = cost_changes_from(db, org, document.id)
    assert (change.item.name, change.before, change.after, change.recipes) == (
        "Milk",
        D("0.2000"),
        D("0.5000"),
        1,  # only the 12 oz uses milk in its version in effect
    )
    found = impact(db, org, item["milk"].id, costing_location(db, org))
    [row] = found.rows
    assert (row.before.per_yield_cents, row.after.per_yield_cents) == (D(40), D(100))
    assert (row.margin_before.amount_cents, row.margin_after.amount_cents) == (D(610), D(550))

    owner = signed_in()
    delivery = owner.get(f"/app/receiving/{document.id}").text
    assert "What this delivery changed in costs" in delivery and "1 recipe affected" in delivery
    page = owner.get(f"/app/catalog/{item['milk'].id}/recipes").text
    assert "Golden State 12 oz" in page and "+$0.60" in page
    assert "$6.10" in page and "$5.50" in page
    member(db, tenant, "employee")
    employee = signed_in("employee@rosewood.test")
    assert employee.get(f"/app/catalog/{item['milk'].id}/recipes").status_code == 403


# --- AC-RCP-08: sold without a recipe ----------------------------------------------------------


def test_ac_rcp_08_sizes_sold_without_a_recipe_are_listed_with_counts_and_a_link(
    db, tenant, location, kitchen
):
    org, item, golden = kitchen
    for name in ("Mango smoothie - Large", "Golden State - 12 oz", "Water", "Cola can"):
        db.add(SoldItem(tenant_id=tenant.id, provider_item_id=name, name=name))
    db.add(  # the POS counts the cans themselves: a sale uses one, no recipe needed
        StockItem(
            tenant_id=tenant.id,
            location_id=location.id,
            slug="cola",
            name="Cola can",
            dimension=Dimension.COUNT,
            stock_unit="each",
            track_mode=TrackMode.PROVIDER,
            provider_variation_id="Cola can",
        )
    )
    db.flush()
    products_from_sold_items(db, org)  # Golden State is taken, so its sale stays unmapped
    db.commit()
    today = business_date(datetime.now(UTC), location.timezone)
    sold = {s.name: s.id for s in db.query(SoldItem)}
    for name, day, units in (
        ("Mango smoothie - Large", today, 7),
        ("Water", today - timedelta(days=2), 3),
        ("Water", today - timedelta(days=40), 50),  # outside 30 days
        ("Cola can", today, 24),
    ):
        sold_on(db, location, db.get(SoldItem, sold[name]), day, units)
    db.commit()
    found = [(u.name, u.units) for u in sold_without_recipe(db, org, datetime.now(UTC))]
    assert found == [("Mango smoothie Large", D(7)), ("Water Regular", D(3))]

    mango = next(u for u in sold_without_recipe(db, org, datetime.now(UTC)) if u.variation_id)
    recipe(db, org, Owner("variation", mango.variation_id), (item["milk"], "300", "ml"))
    page = signed_in().get("/app/products").text
    assert "Sold without a recipe" in page and "Water Regular" in page
    assert "Mango smoothie Large" not in page  # it has a recipe now
    assert "Write the recipe" in page
    member(db, tenant, "employee")
    employee = signed_in("employee@rosewood.test").get("/app/products").text
    assert "Water Regular" in employee and "Write the recipe" not in employee
