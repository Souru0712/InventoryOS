"""T2.09: production runs (WF-11, PRD-FR-01..08, -12, -14, PD-15, AC-PRD-01..03, -05..07)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.batches import date_stock
from app.consumption import consume_sale
from app.costs import cost_on
from app.items import NewItem, create_item
from app.ledger.posting import balance, batch_remaining
from app.models import (
    ExceptionRecord,
    InventoryItem,
    InventoryMovement,
    ItemCostHistory,
    Membership,
    StockBatch,
    StockItem,
    User,
)
from app.production import record_run, void_run
from app.products import NewVariation, create_product, update_product
from app.recipes import NewLine, Owner, save_recipe
from app.sales import add_source, record_sale
from app.security import hash_password
from app.transform.production import ProductionRuleError
from app.transform.sales import SaleLine
from app.units import to_base

D = Decimal
LB = to_base("1", "lb").quantize(D("0.0001"))  # how the recipe stores its 1 lb


def _person(db, tenant, role):
    user = User(
        tenant_id=tenant.id, email=f"{role}@rosewood.test", password_hash=hash_password("x" * 12)
    )
    db.add(user)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
    db.flush()
    return OrgContext(tenant_id=tenant.id, user_id=user.id, permissions=role_permissions(role))


@pytest.fixture
def prep(db, tenant, location):
    owner = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))

    def item(name, dimension, unit, item_type="raw", qty="0", cost="0"):
        return create_item(
            db,
            owner,
            NewItem(
                name=name,
                location_ids=[location.id],
                dimension=dimension,
                display_unit=unit,
                item_type=item_type,
                quantity=qty,
                cost_per_unit=cost,
            ),
        )

    ginger = item("Ginger", "weight", "lb", qty="10", cost="4")
    juice = item("Ginger juice", "volume", "ml", "prepared")
    db.get(InventoryItem, juice.id).default_shelf_life_days = 2
    save_recipe(
        db,
        owner,
        Owner("item", juice.id),
        [NewLine(ginger.id, "1", "lb")],
        yield_quantity="225",
        yield_unit="ml",
    )
    staff = _person(db, tenant, "employee")
    manager = _person(db, tenant, "manager")
    db.commit()
    return owner, staff, manager, ginger, juice, item


def _moves(db, order):
    return {
        (m.inventory_item_id, m.movement_type): m.qty_base
        for m in db.scalars(
            select(InventoryMovement).where(
                InventoryMovement.transaction_id == order.transaction_id
            )
        )
    }


def test_ac_prd_01_a_run_takes_its_inputs_and_makes_a_dated_batch(db, prep, location):
    _owner, staff, _manager, ginger, juice, _item = prep
    order = record_run(
        db, staff, location_id=location.id, inventory_item_id=juice.id, multiplier=D(2)
    )
    db.commit()
    assert _moves(db, order) == {
        (ginger.id, "production_input"): -2 * LB,
        (juice.id, "production_output"): D(450),
    }
    batch = db.get(StockBatch, order.output_batch_id)
    assert batch.expires_on == order.occurred_at.date() + timedelta(days=2)
    assert batch.lot_code == order.batch_code and order.batch_code.startswith("GJ-")
    assert batch_remaining(db, staff, [batch.id]) == {batch.id: D(450)}
    assert (order.expected_yield_base, order.variance_base) == (D(450), D(0))


def test_ac_prd_02_actual_yield_sets_the_variance_and_the_cost(db, prep, location):
    _owner, staff, _manager, ginger, juice, _item = prep
    order = record_run(
        db,
        staff,
        location_id=location.id,
        inventory_item_id=juice.id,
        multiplier=D(2),
        actual_yield="400",
    )
    assert order.variance_base == D(-50)
    # What the 2 lb of ginger cost, over 400 ml (about 2 cents a ml).
    ginger_cost = cost_on(db, staff, location.id, ginger.id, order.occurred_at)
    assert order.unit_cost == (2 * LB * ginger_cost / 400).quantize(D("0.0001"))
    [cost] = db.scalars(
        select(ItemCostHistory).where(
            ItemCostHistory.inventory_item_id == juice.id,
            ItemCostHistory.source_type == "production",
        )
    ).all()
    assert cost.cost_per_base_cents == order.unit_cost


def test_ac_prd_03_an_input_without_a_cost_leaves_the_cost_unknown(db, prep, location):
    owner, staff, _manager, _ginger, _juice, item = prep
    turmeric = item("Turmeric", "weight", "g", qty="500")
    shot = item("Turmeric shot", "volume", "ml", "prepared")
    save_recipe(
        db,
        owner,
        Owner("item", shot.id),
        [NewLine(turmeric.id, "50", "g")],
        yield_quantity="100",
        yield_unit="ml",
    )
    order = record_run(
        db, staff, location_id=location.id, inventory_item_id=shot.id, multiplier=D(1)
    )
    db.commit()
    assert order.unit_cost is None
    assert (
        db.scalars(select(ExceptionRecord).where(ExceptionRecord.type == "missing_cost"))
        .one()
        .status
        == "open"
    )


def test_ac_prd_05_a_void_gives_everything_back_and_empties_the_batch(db, prep, location):
    _owner, staff, manager, ginger, juice, _item = prep
    now = datetime.now(UTC)
    first = record_run(
        db, staff, location_id=location.id, inventory_item_id=juice.id, multiplier=D(1), now=now
    )
    void_run(db, staff, first.id, now=now + timedelta(minutes=10))
    assert balance(db, staff, location.id, ginger.id) == to_base("10", "lb")
    assert batch_remaining(db, staff, [first.output_batch_id])[first.output_batch_id] == D(0)
    second = record_run(
        db, staff, location_id=location.id, inventory_item_id=juice.id, multiplier=D(1), now=now
    )
    later = now + timedelta(minutes=31)
    with pytest.raises(ProductionRuleError, match="a Manager voids it"):
        void_run(db, staff, second.id, now=later)
    with pytest.raises(ProductionRuleError, match="Say why"):
        void_run(db, manager, second.id, now=later)
    void_run(db, manager, second.id, reason="Spilled the batch", now=later)
    assert second.status == "voided"


def test_ac_prd_06_inputs_come_from_the_soonest_expiring_batch(db, prep, location):
    _owner, staff, _manager, ginger, juice, _item = prep
    stock = db.scalars(select(StockItem).where(StockItem.inventory_item_id == ginger.id)).one()
    later = date_stock(db, staff, stock, to_base("3", "lb"), date.today() + timedelta(days=9))
    sooner = date_stock(db, staff, stock, to_base("3", "lb"), date.today() + timedelta(days=2))
    record_run(db, staff, location_id=location.id, inventory_item_id=juice.id, multiplier=D(2))
    assert batch_remaining(db, staff, [later.id, sooner.id]) == {
        later.id: to_base("3", "lb"),
        sooner.id: to_base("3", "lb") - 2 * LB,
    }


def test_pd_15_short_inputs_are_allowed_and_flagged(db, prep, location):
    _owner, staff, _manager, ginger, juice, _item = prep
    record_run(db, staff, location_id=location.id, inventory_item_id=juice.id, multiplier=D(12))
    db.commit()
    assert balance(db, staff, location.id, ginger.id) == to_base("10", "lb") - 12 * LB
    assert (
        db.scalars(select(ExceptionRecord).where(ExceptionRecord.type == "negative_balance"))
        .one()
        .status
        == "open"
    )


def test_ac_prd_07_a_finished_good_sold_by_the_unit_uses_its_batch_only(db, prep, tenant, location):
    owner, staff, _manager, _ginger, juice, item = prep
    bottle = item("Bottled ginger juice", "count", "each", "finished")
    save_recipe(
        db,
        owner,
        Owner("item", bottle.id),
        [NewLine(juice.id, "10", "ml")],
        yield_quantity="1",
        yield_unit="each",
    )
    record_run(db, staff, location_id=location.id, inventory_item_id=juice.id, multiplier=D(2))
    run = record_run(
        db, staff, location_id=location.id, inventory_item_id=bottle.id, multiplier=D(24)
    )
    assert batch_remaining(db, staff, [run.output_batch_id])[run.output_batch_id] == D(24)
    product = create_product(db, owner, name="Ginger bottle", variations=[NewVariation("Bottle")])
    update_product(
        db, owner, product.id, name="Ginger bottle", category="", production_mode="made_ahead"
    )
    save_recipe(
        db, owner, Owner("variation", product.variations[0].id), [NewLine(bottle.id, "1", "")]
    )
    source = add_source(
        db, owner, location_id=location.id, kind="manual", active_from=date(2026, 1, 1)
    )
    juice_before = balance(db, owner, location.id, juice.id)
    sale = record_sale(
        db,
        owner,
        source,
        source_reference="b-1",
        occurred_at=datetime.now(UTC),
        lines=[SaleLine("1", D(1), product.variations[0].id)],
    )
    consume_sale(db, owner, sale.id)
    assert batch_remaining(db, owner, [run.output_batch_id])[run.output_batch_id] == D(23)
    assert balance(db, owner, location.id, juice.id) == juice_before  # no raw ingredients


def test_recording_a_run_through_the_page(db, prep, location):
    _owner, _staff, _manager, _ginger, juice, _item = prep
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        picker = client.get("/app/production")
        assert "Ginger juice" in picker.text
        seen = client.get(f"/app/production?item={juice.id}&times=2")
        assert "It takes" in seen.text and "Ginger" in seen.text
        made = client.post(
            "/app/production",
            data={"location_id": location.id, "inventory_item_id": juice.id, "times": "2"},
        )
        assert "Label the container" in made.text and "Use by" in made.text
