"""T1.22: adjustments and movement history (INV-FR-19, -20, -23, DR-08, AC-INV-08..10)."""

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.adjustments import adjust
from app.exceptions import listing
from app.ledger.posting import balance, post_transaction
from app.main import app
from app.models import (
    ExceptionRecord,
    InventoryAdjustment,
    InventoryTransaction,
    ItemPackage,
    Membership,
    StockItem,
    TrackMode,
    User,
)
from app.orchestration.alerts import evaluate_and_store
from app.security import hash_password
from app.transform.adjustments import AdjustmentRuleError, check_adjustment, needs_review
from app.transform.ledger import BelowZero, Line, crosses_zero
from app.units import Dimension
from tests.ledger_helpers import opened

D = Decimal
PASSWORD = "a-good-password"  # noqa: S105 (the test accounts', as in conftest)


@pytest.fixture
def bars(db, tenant, location):
    """Ten protein bars at Main, at $3.00 each."""
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="bars",
        name="Protein bars",
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.MANUAL,
        qty_base=D(10),
        cost_per_base_cents=D(300),
    )
    db.add(item)
    opened(db, item)
    db.commit()
    return item


def person(db, tenant, role: str) -> TestClient:
    user = User(
        tenant_id=tenant.id, email=f"{role}@rosewood.test", password_hash=hash_password(PASSWORD)
    )
    db.add(user)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
    db.commit()
    client = TestClient(app)
    client.__enter__()
    client.post("/login", data={"email": user.email, "password": PASSWORD})
    return client


def member(db, tenant, role: str) -> OrgContext:
    user = db.scalars(select(User).where(User.email == f"{role}@rosewood.test")).one()
    return OrgContext(tenant_id=tenant.id, user_id=user.id, permissions=role_permissions(role))


def form(location, **fields):
    return {
        "location_id": location.id,
        "direction": "remove",
        "quantity": "2",
        "unit": "each",
        "reason": "correction",
        **fields,
    }


def on_hand(db, item) -> Decimal:
    db.expire_all()
    return balance(db, OrgContext.system(item.tenant_id), item.location_id, item.inventory_item_id)


# --- the rules (pure) --------------------------------------------------------------------------


def test_an_adjustment_says_why_and_an_override_says_why_again():
    for qty, reason, note, override, why in (
        (D(0), "found", None, False, None),
        (D(1), "lost_it", None, False, None),
        (D(1), "other", "  ", False, None),
        (D(-1), "correction", None, True, ""),
    ):
        with pytest.raises(AdjustmentRuleError):
            check_adjustment(qty, reason, note, override=override, override_reason=why)
    checked = check_adjustment(D(-2), "other", " broke  two ", override=True, override_reason="x")
    assert (checked.note, checked.override_reason) == ("broke two", "x")


def test_stock_may_not_be_taken_below_zero_but_negative_stock_may_rise():
    pair = (1, 7)
    assert crosses_zero({pair: D(5)}, [Line(*pair, "adjustment", D(-6))]) == [pair]
    assert crosses_zero({pair: D(5)}, [Line(*pair, "adjustment", D(-5))]) == []
    assert crosses_zero({pair: D(-3)}, [Line(*pair, "adjustment", D(2))]) == []
    assert crosses_zero({pair: D(-3)}, [Line(*pair, "adjustment", D(-1))]) == [pair]


def test_fifty_dollars_or_more_is_reviewed():
    assert needs_review(D(-17), D(300)) is True  # $51
    assert needs_review(D(16), D(300)) is False  # $48


# --- AC-INV-08: posted with a reason, and in the history ---------------------------------------


def test_ac_inv_08_an_adjustment_posts_with_its_reason_and_shows_in_history(
    db, tenant, location, bars
):
    manager = person(db, tenant, "manager")
    page = manager.post(
        f"/app/catalog/{bars.inventory_item_id}/adjust",
        data=form(location, note="two bars were open"),
    )
    assert "Adjusted Protein bars at Main by -2." in page.text  # back on the history page
    assert on_hand(db, bars) == D(8)

    row = db.scalars(select(InventoryAdjustment)).one()
    txn = db.scalars(
        select(InventoryTransaction).where(InventoryTransaction.kind == "adjustment")
    ).one()
    assert txn.idempotency_key == f"adjustment:{row.id}"
    assert (row.transaction_id, row.reason, row.note, row.value_cents) == (
        txn.id,
        "correction",
        "two bars were open",
        600,
    )
    assert txn.actor_user_id == member(db, tenant, "manager").user_id

    for expected in ("Adjustment", "manager@rosewood.test", "Correction: two bars were open", "-2"):
        assert expected in page.text
    # Newest first, each row with the balance after it: 8 after the adjustment, 10 after opening.
    assert page.text.index("Adjustment") < page.text.index("Opening balance")


def test_an_adjustment_without_a_reason_posts_nothing(db, tenant, location, bars):
    manager = person(db, tenant, "manager")
    refused = manager.post(
        f"/app/catalog/{bars.inventory_item_id}/adjust", data=form(location, reason="")
    )
    assert refused.status_code == 400 and "Pick a reason" in refused.text
    assert on_hand(db, bars) == D(10)
    assert db.scalars(select(InventoryAdjustment)).all() == []


def test_an_adjustment_can_be_entered_in_a_package(db, tenant, location, bars):
    db.add(
        ItemPackage(
            tenant_id=tenant.id,
            inventory_item_id=bars.inventory_item_id,
            name="box",
            quantity=D(12),
            unit="each",
        )
    )
    db.commit()
    manager = person(db, tenant, "manager")
    manager.post(
        f"/app/catalog/{bars.inventory_item_id}/adjust",
        data=form(location, direction="add", quantity="1", unit="box", reason="found"),
    )
    assert on_hand(db, bars) == D(22)


# --- AC-INV-09: below zero only with an override (DR-08) ---------------------------------------


def test_ac_inv_09_below_zero_needs_an_override_with_a_reason(db, tenant, location, bars):
    manager = person(db, tenant, "manager")
    url = f"/app/catalog/{bars.inventory_item_id}/adjust"

    refused = manager.post(url, data=form(location, quantity="15"))
    assert refused.status_code == 400 and "below zero" in refused.text
    assert on_hand(db, bars) == D(10)
    assert db.scalars(select(InventoryAdjustment)).all() == []  # the refused one is not kept

    bare = manager.post(url, data=form(location, quantity="15", override="yes"))
    assert bare.status_code == 400 and "Say why stock may go below zero" in bare.text

    manager.post(
        url, data=form(location, quantity="15", override="yes", override_reason="till miscounted")
    )
    assert on_hand(db, bars) == D(-5)
    negative = db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.type == "negative_balance")
    ).one()
    assert negative.status == "open" and "count required" in negative.title
    assert db.scalars(select(InventoryAdjustment)).one().override_reason == "till miscounted"


def test_an_employee_may_not_adjust_or_take_stock_below_zero(db, tenant, location, bars):
    employee = person(db, tenant, "employee")
    url = f"/app/catalog/{bars.inventory_item_id}/adjust"
    assert employee.get(url).status_code == 403
    assert employee.post(url, data=form(location)).status_code == 403
    assert "Adjust stock" not in employee.get(f"/app/catalog/{bars.inventory_item_id}").text
    # Even given the adjustment key, the override is a separate one (DR-08).
    org = OrgContext(
        tenant_id=tenant.id, permissions=frozenset({"adjustment.create", "inventory.view"})
    )
    with pytest.raises(AdjustmentRuleError, match="Only a Manager"):
        adjust(
            db,
            org,
            inventory_item_id=bars.inventory_item_id,
            location_id=location.id,
            qty_base=D(-15),
            reason="correction",
            override=True,
            override_reason="trust me",
        )


def test_the_ledger_refuses_a_manual_movement_below_zero(db, tenant, location, bars):
    """The same rule waste will use (T2.07): checked while the balance is locked."""
    with pytest.raises(BelowZero):
        post_transaction(
            db,
            OrgContext.system(tenant.id),
            kind="waste",
            source_type="test",
            source_id="1",
            idempotency_key="waste",
            lines=[Line(location.id, bars.inventory_item_id, "waste", D(-11))],
            occurred_at=datetime.now(UTC),
            refuse_below_zero=True,
        )
    db.rollback()
    assert on_hand(db, bars) == D(10)


# --- AC-INV-10: a sale posts in full, then a count is required ---------------------------------


def test_ac_inv_10_a_sale_larger_than_on_hand_posts_and_asks_for_a_count(
    db, tenant, location, bars
):
    org = OrgContext.system(tenant.id)
    post_transaction(
        db,
        org,
        kind="sale",
        source_type="test",
        source_id="order-1",
        idempotency_key="sale:order-1",
        lines=[Line(location.id, bars.inventory_item_id, "sale_consumption", D(-14))],
        occurred_at=datetime.now(UTC),
    )
    evaluate_and_store(db, org)
    db.commit()
    assert on_hand(db, bars) == D(-4)  # posted in full
    negative = db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.type == "negative_balance")
    ).one()
    assert negative.title == "Protein bars at Main is below zero (-4 each): count required."
    owner = TestClient(app)
    owner.__enter__()
    owner.post("/login", data={"email": "owner@rosewood.test", "password": PASSWORD})
    assert "count required" in owner.get("/app").text

    owner.post(f"/app/items/{bars.id}/count", data={"quantity": "3"})
    db.commit()  # a fresh read, as the evaluation job has
    db.expire_all()
    assert on_hand(db, bars) == D(3)
    evaluate_and_store(db, org)
    db.commit()
    db.expire_all()
    assert db.get(ExceptionRecord, negative.id).resolution == "system"


# --- review above the threshold (STF-FR-08, R1) ------------------------------------------------


def test_a_large_adjustment_posts_and_waits_for_an_owner_review(db, tenant, location, bars):
    person(db, tenant, "manager")
    person(db, tenant, "employee")
    manager = member(db, tenant, "manager")
    adjust(
        db,
        manager,
        inventory_item_id=bars.inventory_item_id,
        location_id=location.id,
        qty_base=D(20),
        reason="found",
    )
    adjust(
        db,
        manager,
        inventory_item_id=bars.inventory_item_id,
        location_id=location.id,
        qty_base=D(1),
        reason="found",
    )
    db.commit()
    [review] = db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.type == "adjustment_review")
    ).all()
    assert review.money_impact_cents == 6000 and "$60.00" in review.title
    assert on_hand(db, bars) == D(31)  # posted, reviewed afterwards
    # Financial: an Employee does not see it at all (REC-FR-16).
    assert all(v.type != "adjustment_review" for v in listing(db, member(db, tenant, "employee")))


# --- the history (INV-FR-20) -------------------------------------------------------------------


def test_the_history_filters_and_keeps_true_running_balances(db, tenant, location, bars):
    person(db, tenant, "manager")
    manager = member(db, tenant, "manager")
    yesterday = datetime.now(UTC) - timedelta(days=1)
    adjust(
        db,
        manager,
        inventory_item_id=bars.inventory_item_id,
        location_id=location.id,
        qty_base=D(5),
        reason="found",
        now=yesterday,
    )
    adjust(
        db,
        manager,
        inventory_item_id=bars.inventory_item_id,
        location_id=location.id,
        qty_base=D(-3),
        reason="sample",
    )
    db.commit()
    owner = TestClient(app)
    owner.__enter__()
    owner.post("/login", data={"email": "owner@rosewood.test", "password": PASSWORD})
    url = f"/app/catalog/{bars.inventory_item_id}/history"

    everything = owner.get(url).text
    assert everything.count("<tr>") == 1 + 3  # header + opening, found, sample
    only_samples = owner.get(url, params={"type": "adjustment", "actor": manager.user_id}).text
    assert "Sample" in only_samples and "Opening balance" not in only_samples
    # Filtered or not, the balance after the sample is 12 (10 + 5 - 3), not 2.
    assert ">12 each</td>" in only_samples
    today = owner.get(url, params={"since": datetime.now(UTC).date().isoformat()}).text
    assert "Sample" in today and "Found" not in today
    assert "-$9.00" in everything  # the owner sees what the sample was worth


def test_nothing_else_creates_adjustments():
    found = [
        path.as_posix()
        for path in Path("app").rglob("*.py")
        if path.as_posix() != "app/adjustments.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "InventoryAdjustment"
    ]
    assert found == []
