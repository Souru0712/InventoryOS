"""T1.24: cost basis and valuation (ANL-FR-01, -02, INV-FR-07, -24, DR-19, PD-08, AC-ANL-01, -02)."""

import ast
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import OrgContext, UnscopedAccess, update_scoped
from app.access.permissions import role_permissions
from app.adjustments import adjust
from app.costs import history, set_cost, value_stock
from app.counts import post_count, record_line, start_count
from app.items import NewItem, create_item
from app.ledger.posting import post_transaction
from app.main import app
from app.models import (
    AuditEvent,
    InventoryAdjustment,
    InventoryMovement,
    ItemCostHistory,
    Membership,
    StockCountLine,
    StockItem,
    TrackMode,
    User,
)
from app.security import hash_password
from app.transform.costs import CostRuleError, Holding, check_cost, cost_at, valuation, value_of
from app.transform.ledger import Line
from app.units import Dimension
from tests.ledger_helpers import opened

D = Decimal
PASSWORD = "a-good-password"  # noqa: S105 (the test accounts', as in conftest)


def stock(db, location, name="Milk", qty="10", cost=None):
    item = StockItem(
        tenant_id=location.tenant_id,
        location_id=location.id,
        slug=name.lower(),
        name=name,
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.MANUAL,
    )
    db.add(item)
    db.flush()
    org = OrgContext.system(location.tenant_id)
    if cost is not None:
        set_cost(
            db,
            org,
            location_id=location.id,
            inventory_item_id=item.inventory_item_id,
            cost=D(cost),
            source_type="opening",
        )
    item.qty_base = D(qty)  # opened() posts it as the opening balance
    opened(db, item)
    db.commit()
    return item


def manager(db, tenant):
    user = User(
        tenant_id=tenant.id, email="manager@rosewood.test", password_hash=hash_password(PASSWORD)
    )
    db.add(user)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role="manager"))
    db.commit()
    return OrgContext(tenant_id=tenant.id, user_id=user.id, permissions=role_permissions("manager"))


def signed_in(email="owner@rosewood.test") -> TestClient:
    client = TestClient(app)
    client.__enter__()
    client.post("/login", data={"email": email, "password": PASSWORD})
    return client


# --- the rules (pure) --------------------------------------------------------------------------


def test_value_is_what_is_held_times_its_cost_and_below_zero_is_nothing():
    assert value_of(D(10), D("2.5")) == 25
    assert value_of(D(-4), D(100)) == 0
    assert value_of(D(3), None) == 0
    result = valuation(
        [
            Holding(1, 1, "Milk", D(10), D(100)),
            Holding(1, 2, "Oats", D(-2), D(50)),
            Holding(1, 3, "Cups", D(40), None),  # held, no cost: listed, not hidden
            Holding(1, 4, "Lids", D(0), None),  # none held: nothing missing
        ]
    )
    assert result.total_cents == 1000
    assert [h.name for h in result.without_cost] == ["Cups"]
    assert result.basis == "latest receipt cost, unless overridden"


def test_the_cost_in_effect_is_the_latest_at_or_before_the_instant():
    t = datetime(2026, 11, 1, tzinfo=UTC)
    entries = [(t, D(100)), (t + timedelta(days=3), D(200))]
    assert cost_at(entries, t - timedelta(seconds=1)) is None
    assert cost_at(entries, t + timedelta(days=1)) == D(100)
    assert cost_at(entries, t + timedelta(days=3)) == D(200)
    with pytest.raises(CostRuleError):
        check_cost(D(-1))


# --- AC-ANL-01: today's cost never changes past figures ----------------------------------------


def test_ac_anl_01_changing_the_current_cost_changes_no_past_figure(db, tenant, location):
    milk = stock(db, location, qty="10", cost="100")
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    item_id = milk.inventory_item_id
    adjust(
        db, org, inventory_item_id=item_id, location_id=location.id, qty_base=D(-2), reason="sample"
    )
    session = start_count(db, org, location_id=location.id, scope="full")
    record_line(db, org, session.id, item_id, [("6", "each")])
    post_count(db, org, session.id)
    db.commit()
    before = (
        sorted((m.id, m.unit_cost_snapshot) for m in db.scalars(select(InventoryMovement))),
        db.scalars(select(InventoryAdjustment)).one().value_cents,
        db.scalars(select(StockCountLine)).one().variance_cents,
    )
    yesterday = value_stock(db, org, as_of_day=date.today() - timedelta(days=1)).total_cents

    set_cost(
        db,
        org,
        location_id=location.id,
        inventory_item_id=item_id,
        cost=D(900),
        source_type="override",
    )
    db.commit()

    db.expire_all()
    after = (
        sorted((m.id, m.unit_cost_snapshot) for m in db.scalars(select(InventoryMovement))),
        db.scalars(select(InventoryAdjustment)).one().value_cents,
        db.scalars(select(StockCountLine)).one().variance_cents,
    )
    assert after == before  # snapshots, waste-like and variance figures: all as posted
    assert before[1] == 200 and before[2] == -200  # -2 at $1.00, then -2 more at $1.00
    assert value_stock(db, org, as_of_day=date.today() - timedelta(days=1)).total_cents == yesterday
    assert value_stock(db, org).total_cents == 6 * 900  # only the current valuation moves


def test_the_history_page_keeps_the_cost_each_movement_was_posted_at(db, tenant, location):
    milk = stock(db, location, qty="10", cost="100")
    set_cost(
        db,
        OrgContext.system(tenant.id),
        location_id=location.id,
        inventory_item_id=milk.inventory_item_id,
        cost=D(500),
        source_type="override",
    )
    db.commit()
    page = signed_in().get(f"/app/catalog/{milk.inventory_item_id}/history").text
    assert "$10.00" in page  # the opening: 10 at $1.00, not at today's $5.00


# --- AC-ANL-02: valuation states its basis and what has no cost --------------------------------


def test_ac_anl_02_valuation_is_held_times_current_cost_with_its_basis(db, tenant, location):
    stock(db, location, "Milk", "10", cost="100")
    oats = stock(db, location, "Oats", "5", cost="50")
    stock(db, location, "Cups", "40")  # no cost
    post_transaction(
        db,
        OrgContext.system(tenant.id),
        kind="sale",
        source_type="test",
        source_id="1",
        idempotency_key="sale",
        lines=[Line(location.id, oats.inventory_item_id, "sale_consumption", D(-8))],
        occurred_at=datetime.now(UTC),
    )
    db.commit()
    result = value_stock(db, OrgContext.system(tenant.id))
    assert result.total_cents == 1000  # milk 10 x $1; oats at -3 hold nothing; cups unknown
    assert [h.name for h in result.without_cost] == ["Cups"]

    owner = signed_in()
    page = owner.get("/app/valuation").text
    assert "$10.00" in page and "latest receipt cost, unless overridden" in page
    assert "Held without a cost, so not in the total: Cups" in page
    dashboard = owner.get("/app").text
    assert "At latest receipt cost, unless overridden; 1 held without a cost." in dashboard


def test_an_as_of_valuation_uses_what_was_held_and_the_cost_then(db, tenant, location):
    milk = stock(db, location, qty="0")
    org = OrgContext.system(tenant.id)
    day1, day5 = datetime(2026, 11, 1, 12, tzinfo=UTC), datetime(2026, 11, 5, 12, tzinfo=UTC)
    for when, qty, cost in ((day1, "10", "100"), (day5, "5", "200")):
        set_cost(
            db,
            org,
            location_id=location.id,
            inventory_item_id=milk.inventory_item_id,
            cost=D(cost),
            source_type="receipt",
            effective_at=when,
        )
        post_transaction(
            db,
            org,
            kind="receipt",
            source_type="test",
            source_id=str(when),
            idempotency_key=f"r:{when}",
            lines=[Line(location.id, milk.inventory_item_id, "receipt", D(qty), unit_cost=D(cost))],
            occurred_at=when,
        )
    db.commit()
    assert value_stock(db, org, as_of_day=date(2026, 11, 2)).total_cents == 10 * 100
    assert value_stock(db, org, as_of_day=date(2026, 10, 31)).total_cents == 0
    assert value_stock(db, org).total_cents == 15 * 200


# --- where a current cost comes from (INV-FR-07) -----------------------------------------------


def test_a_new_item_opens_at_its_cost_and_its_history_starts(db, tenant, location):
    org = OrgContext.system(tenant.id)
    item = create_item(
        db,
        org,
        NewItem(
            name="Oat milk",
            location_ids=[location.id],
            dimension="count",
            display_unit="each",
            quantity="6",
            cost_per_unit="2.50",
        ),
    )
    db.commit()
    [entry] = history(db, org, location.id, item.id)
    assert (entry.source_type, entry.cost_per_base_cents) == ("opening", D(250))
    opening = db.scalars(select(InventoryMovement)).one()
    assert opening.unit_cost_snapshot == D(250)  # posted at the cost it opened with


def test_setting_the_same_cost_again_adds_no_history(db, tenant, location):
    milk = stock(db, location, cost="100")
    org = OrgContext.system(tenant.id)
    assert (
        set_cost(
            db,
            org,
            location_id=location.id,
            inventory_item_id=milk.inventory_item_id,
            cost=D(100),
            source_type="receipt",
        )
        is None
    )
    assert len(history(db, org, location.id, milk.inventory_item_id)) == 1


def test_a_manager_overrides_the_cost_and_it_is_recorded(db, tenant, location):
    milk = stock(db, location, cost="100")
    manager(db, tenant)
    client = signed_in("manager@rosewood.test")
    page = client.post(
        f"/app/catalog/{milk.inventory_item_id}/cost",
        data={"location_id": location.id, "price": "1.75", "unit": "each"},
    ).text
    assert "Past figures keep their cost." in page
    db.expire_all()
    assert db.get(StockItem, milk.id).cost_per_base_cents == D(175)
    latest = history(db, OrgContext.system(tenant.id), location.id, milk.inventory_item_id)[0]
    assert latest.source_type == "override"
    event = db.scalars(select(AuditEvent)).one()
    assert (event.action, event.after) == ("item.cost_changed", {"cost_per_base_cents": "175.0000"})


def test_an_employee_cannot_override_a_cost(db, tenant, location):
    milk = stock(db, location, cost="100")
    user = User(
        tenant_id=tenant.id, email="employee@rosewood.test", password_hash=hash_password(PASSWORD)
    )
    db.add(user)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role="employee"))
    db.commit()
    client = signed_in("employee@rosewood.test")
    response = client.post(
        f"/app/catalog/{milk.inventory_item_id}/cost",
        data={"location_id": location.id, "price": "9.99", "unit": "each"},
    )
    assert response.status_code == 403
    assert client.get("/app/valuation").status_code == 403


# --- append-only, one writer -------------------------------------------------------------------


def test_cost_history_is_append_only_and_written_in_one_place():
    with pytest.raises(UnscopedAccess, match="append-only"):
        update_scoped(OrgContext.system(1), ItemCostHistory)
    found = []
    for path in sorted(Path("app").rglob("*.py")):
        where = path.as_posix()
        if where == "app/costs.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign | ast.AugAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr == "cost_per_base_cents":
                        found.append(f"{where}:{node.lineno}")
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name == "ItemCostHistory" or (
                    name == "StockItem"
                    and any(k.arg == "cost_per_base_cents" for k in node.keywords)
                ):
                    found.append(f"{where}:{node.lineno} {name}(")
    assert found == []
