"""T1.23-a: count sessions, model and posting (REC-FR-01..03, -05..08, DR-12, AC-REC-01..04)."""

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.counts import NotFound, count_items, post_count, record_line, start_count
from app.db import SessionLocal
from app.exceptions import listing
from app.ledger.posting import balance, movements_total, post_transaction
from app.models import (
    ExceptionRecord,
    InventoryCount,
    InventoryMovement,
    InventoryTransaction,
    ItemLocation,
    ItemPackage,
    StockCount,
    StockCountLine,
    StockItem,
    TrackMode,
)
from app.transform.counts import CountRuleError, check_parts, check_scope, needs_review
from app.transform.ledger import Line
from app.units import Dimension
from tests.ledger_helpers import opened

D = Decimal
# Counted a little after the fixtures open their stock (tests.ledger_helpers dates openings now).
T0 = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=5)


def stock(db, location, name, qty, cost="100", area=None):
    item = StockItem(
        tenant_id=location.tenant_id,
        location_id=location.id,
        slug=name.lower(),
        name=name,
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.MANUAL,
        qty_base=D(qty),
        cost_per_base_cents=D(cost),
    )
    db.add(item)
    opened(db, item)
    if area is not None:
        db.get(ItemLocation, item.item_location_id).storage_area = area
    db.commit()
    return item


def post(db, org, item, movement_type, qty, when, key):
    post_transaction(
        db,
        org,
        kind={"receipt": "receipt", "sale_consumption": "sale"}[movement_type],
        source_type="test",
        source_id=key,
        idempotency_key=key,
        lines=[Line(item.location_id, item.inventory_item_id, movement_type, D(qty))],
        occurred_at=when,
    )


@pytest.fixture
def org(tenant):
    return OrgContext(tenant_id=tenant.id, user_id=None, permissions=role_permissions("owner"))


# --- the rules (pure) --------------------------------------------------------------------------


def test_a_session_covers_one_thing_and_a_line_is_never_below_zero():
    assert check_scope("full", "ignored") is None
    assert check_scope("area", "  Walk-in  cooler ") == "Walk-in cooler"
    for scope, ref in (("everything", None), ("area", " "), ("item", None)):
        with pytest.raises(CountRuleError):
            check_scope(scope, ref)
    assert check_parts([D(36), D(4)]) == D(40)
    for parts in ([], [D(1), D(-1)]):
        with pytest.raises(CountRuleError):
            check_parts(parts)


def test_ac_rec_04_review_above_25_dollars_or_20_percent():
    assert needs_review(D(-4), D(10), -4000) is True  # -$40
    assert needs_review(D(-1), D(10), -1000) is False  # -$10, 10%
    assert needs_review(D(-3), D(10), -300) is True  # 30% of what was expected
    assert needs_review(D(2), D(0), 200) is False  # nothing expected: value decides
    assert needs_review(D(0), D(10), 0) is False


# --- AC-REC-01: storage order, packages + loose ------------------------------------------------


def test_ac_rec_01_a_storage_area_count_lists_in_order_and_takes_cases_plus_each(
    db, tenant, location, org
):
    lemons = stock(db, location, "Lemons", "10", area="Bar")
    stock(db, location, "Ale", "5", area="Bar")
    milk = stock(db, location, "Milk", "8", area="Walk-in")
    stock(db, location, "Cups", "50")
    db.add(
        ItemPackage(
            tenant_id=tenant.id,
            inventory_item_id=lemons.inventory_item_id,
            name="case",
            quantity=D(12),
            unit="each",
        )
    )
    db.commit()

    everything = start_count(db, org, location_id=location.id, scope="full")
    assert [c.item.name for c in count_items(db, org, everything)] == [
        "Ale",
        "Lemons",
        "Milk",
        "Cups",  # no storage area: last
    ]
    bar = start_count(db, org, location_id=location.id, scope="area", scope_ref="Bar")
    assert [c.item.name for c in count_items(db, org, bar)] == ["Ale", "Lemons"]

    line = record_line(
        db, org, bar.id, lemons.inventory_item_id, [("3", "case"), ("4", "each")], now=T0
    )
    assert (line.counted_qty_base, line.entered) == (D(40), "3 case + 4 each")
    with pytest.raises(NotFound):  # milk lives in the walk-in, not at the bar
        record_line(db, org, bar.id, milk.inventory_item_id, [("1", "each")])


def test_an_item_outside_the_session_cannot_be_counted_in_it(db, tenant, location, org):
    milk = stock(db, location, "Milk", "8", area="Walk-in")
    stock(db, location, "Ale", "5", area="Bar")
    bar = start_count(db, org, location_id=location.id, scope="area", scope_ref="Bar")
    with pytest.raises(NotFound):
        record_line(db, org, bar.id, milk.inventory_item_id, [("8", "each")])
    single = start_count(
        db, org, location_id=location.id, scope="item", scope_ref=str(milk.inventory_item_id)
    )
    assert [c.item.name for c in count_items(db, org, single)] == ["Milk"]


# --- AC-REC-02: expected is the balance at the line's instant ---------------------------------


def test_ac_rec_02_expected_is_the_balance_when_counted_whatever_posts_around_it(
    db, tenant, location, org
):
    milk = stock(db, location, "Milk", "0")
    post(db, org, milk, "receipt", "20", T0 - timedelta(hours=2), "r1")
    session = start_count(db, org, location_id=location.id, scope="full", now=T0)
    record_line(db, org, session.id, milk.inventory_item_id, [("17", "each")], now=T0)
    # A sale one minute after the count, and a delivery that happened before the count but
    # was only recorded after it, both posted while the session is open.
    post(db, org, milk, "sale_consumption", "-2", T0 + timedelta(minutes=1), "s1")
    post(db, org, milk, "receipt", "6", T0 - timedelta(minutes=30), "r2")
    db.commit()

    post_count(db, org, session.id, now=T0 + timedelta(minutes=10))
    db.commit()
    [line] = db.scalars(select(StockCountLine)).all()
    # Expected: 20 + 6 (both before 09:00); the sale after the count is not in it.
    assert (line.expected_qty_base, line.variance_qty_base) == (D(26), D(-9))
    movement = db.get(InventoryMovement, line.movement_id)
    assert (movement.movement_type, movement.qty_base, movement.occurred_at) == (
        "count_variance",
        D(-9),
        T0,
    )
    pair = (location.id, milk.inventory_item_id)
    # 17 on the shelf at 09:00, then 2 sold: 15 now, and the ledger agrees with itself.
    assert balance(db, org, *pair) == movements_total(db, org, *pair) == D(15)
    db.expire_all()
    assert db.get(StockItem, milk.id).qty_base == D(15)
    assert db.get(StockItem, milk.id).last_counted_at == T0


# --- AC-REC-03: one variance per non-zero line, observations kept ------------------------------


def test_ac_rec_03_one_count_variance_per_non_zero_line_and_none_for_zero(
    db, tenant, location, org
):
    milk = stock(db, location, "Milk", "8")
    ale = stock(db, location, "Ale", "5")
    cups = stock(db, location, "Cups", "50")
    session = start_count(db, org, location_id=location.id, scope="full", now=T0)
    record_line(db, org, session.id, milk.inventory_item_id, [("6", "each")], now=T0)
    record_line(db, org, session.id, ale.inventory_item_id, [("5", "each")], now=T0)
    record_line(db, org, session.id, cups.inventory_item_id, [("49", "each")], now=T0)
    record_line(db, org, session.id, cups.inventory_item_id, [("52", "each")], now=T0)  # recount
    post_count(db, org, session.id, now=T0)
    db.commit()

    [txn] = db.scalars(
        select(InventoryTransaction).where(InventoryTransaction.kind == "count")
    ).all()
    assert txn.idempotency_key == f"count:{session.id}"
    assert sorted(m.qty_base for m in txn.movements) == [D(-2), D(2)]
    lines = {line.inventory_item_id: line for line in db.scalars(select(StockCountLine))}
    assert len(lines) == 3
    assert lines[ale.inventory_item_id].movement_id is None  # matched: no movement
    assert lines[cups.inventory_item_id].counted_qty_base == D(52)  # the observation kept
    assert db.scalars(select(InventoryCount)).all() == []  # DM-7: the legacy table stays frozen
    db.expire_all()
    count = db.get(StockCount, session.id)
    assert (count.status, count.transaction_id) == ("posted", txn.id)


def test_a_count_with_no_difference_posts_without_a_transaction(db, tenant, location, org):
    milk = stock(db, location, "Milk", "8")
    session = start_count(db, org, location_id=location.id, scope="full", now=T0)
    record_line(db, org, session.id, milk.inventory_item_id, [("8", "each")], now=T0)
    post_count(db, org, session.id, now=T0)
    db.commit()
    assert db.get(StockCount, session.id).transaction_id is None
    assert (
        db.scalars(select(InventoryTransaction).where(InventoryTransaction.kind == "count")).all()
        == []
    )


def test_a_session_posts_once_even_when_two_requests_saw_it_open(db, tenant, location, org):
    milk = stock(db, location, "Milk", "8")
    session = start_count(db, org, location_id=location.id, scope="full", now=T0)
    record_line(db, org, session.id, milk.inventory_item_id, [("5", "each")], now=T0)
    db.commit()
    first, second = SessionLocal(), SessionLocal()
    try:
        assert first.get(StockCount, session.id).status == second.get(StockCount, session.id).status
        post_count(first, org, session.id, now=T0)
        first.commit()
        with pytest.raises(CountRuleError, match="already been posted"):
            post_count(second, org, session.id, now=T0)
        second.rollback()
    finally:
        first.close()
        second.close()
    with pytest.raises(CountRuleError, match="has been posted"):
        record_line(db, org, session.id, milk.inventory_item_id, [("1", "each")])
    db.expire_all()
    assert balance(db, org, location.id, milk.inventory_item_id) == D(5)


def test_an_empty_session_cannot_be_posted(db, tenant, location, org):
    session = start_count(db, org, location_id=location.id, scope="full")
    with pytest.raises(CountRuleError, match="at least one item"):
        post_count(db, org, session.id)


# --- AC-REC-04: review after posting -----------------------------------------------------------


def test_ac_rec_04_a_40_dollar_shortfall_opens_a_review_and_a_10_dollar_one_does_not(
    db, tenant, location, org
):
    vodka = stock(db, location, "Vodka", "10", cost="1000")  # $10 each
    gin = stock(db, location, "Gin", "100", cost="1000")
    session = start_count(db, org, location_id=location.id, scope="full", now=T0)
    record_line(db, org, session.id, vodka.inventory_item_id, [("6", "each")], now=T0)  # -$40
    record_line(db, org, session.id, gin.inventory_item_id, [("99", "each")], now=T0)  # -$10
    post_count(db, org, session.id, now=T0)
    db.commit()

    lines = {line.inventory_item_id: line for line in db.scalars(select(StockCountLine))}
    assert lines[vodka.inventory_item_id].variance_cents == -4000
    assert lines[vodka.inventory_item_id].needs_review is True
    assert (
        lines[gin.inventory_item_id].variance_cents,
        lines[gin.inventory_item_id].needs_review,
    ) == (
        -1000,
        False,
    )
    [review] = db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.type == "count_variance_high")
    ).all()
    assert review.subject_id == str(lines[vodka.inventory_item_id].id)
    assert review.title == (
        "Vodka at Main: counted 6 each, expected 10 each (-4). Review the count."
    )
    assert review.money_impact_cents == 4000
    employee = OrgContext(tenant_id=tenant.id, permissions=role_permissions("employee"))
    [seen] = listing(db, employee)
    assert seen.money_impact_cents is None  # costs hidden from Employees


# --- nothing else writes them ------------------------------------------------------------------


def test_only_the_counts_module_creates_sessions_and_lines():
    found = [
        f"{path.as_posix()}:{node.lineno}"
        for path in Path("app").rglob("*.py")
        if path.as_posix() != "app/counts.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") in {"StockCount", "StockCountLine"}
    ]
    assert found == []
