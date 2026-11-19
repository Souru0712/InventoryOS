"""T2.15: reorder suggestions on ledger usage (ROR-FR-01..05, -07, -08, STD-FR-09, AC-ROR-01..05)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.items import NewItem, create_item
from app.models import StockItem
from app.reorder import plan
from app.sales import add_source
from app.suppliers import SupplierFields, add_supplier_item, create_supplier
from app.transform.business_days import business_date
from app.transform.reorder import (
    Inputs,
    Move,
    Observation,
    count_based_use,
    count_first,
    daily_use,
    observations,
    order_by,
    suggest,
)
from tests.ledger_helpers import consumed_on, counted_to

D = Decimal
NOW = datetime(2026, 11, 19, 15, 0, tzinfo=UTC)


# --- the rules ------------------------------------------------------------------------------------


def test_ac_ror_01_the_worked_example():
    inputs = Inputs(
        per_day=D(2000),
        lead_days=2,
        cover_days=3,
        safety_percent=D(15),
        reorder_point=D(3000),
        on_hand=D(5000),
        pack=D(5000),
    )
    made = suggest(inputs)
    assert (made.raw, made.quantity, made.packs) == (D(9500), D(10000), 2)


def test_without_a_pack_counted_items_order_whole_units():
    inputs = Inputs(D("1.3"), 1, 2, D(0), D(2), D(1), whole_units=True)
    assert suggest(inputs).quantity == D(5)  # 1.3 x 3 + 2 - 1 = 4.9


def test_enough_stock_orders_nothing():
    assert suggest(Inputs(D(1), 1, 1, D(0), D(0), D(50))).quantity == 0


def _move(day, kind, movement_type, qty, txn, source="x"):
    at = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12)
    return Move(at, day, txn, kind, source, movement_type, D(qty))


def test_ac_anl_08_use_from_two_counts():
    """100 counted on day 1, 60 received, 10 wasted, 40 counted on day 8: 110 used."""
    d1, d8 = date(2026, 11, 1), date(2026, 11, 8)
    moves = [
        _move(d1, "count", "count_variance", "100", 1, "count"),
        _move(d1 + timedelta(days=2), "receipt", "receipt", "60", 2),
        _move(d1 + timedelta(days=3), "waste", "waste", "-10", 3),
        _move(d1 + timedelta(days=4), "sale", "sale_consumption", "-30", 4),
        _move(d8, "count", "count_variance", "-80", 5, "count"),
    ]
    counts = observations(D(0), moves, [])
    used = count_based_use(moves, counts)
    assert used is not None
    assert (used.used, used.days, used.per_day) == (D(110), 7, D("15.7143"))


def test_a_count_session_line_that_matched_still_counts():
    d1, d8 = date(2026, 11, 1), date(2026, 11, 8)
    at = datetime(2026, 11, 8, 18, tzinfo=UTC)
    moves = [_move(d1, "count", "count_variance", "50", 1, "count")]
    counts = observations(D(0), moves, [Observation(at, d8, D(15))])
    used = count_based_use(moves, counts)
    assert used is not None and used.used == D(35)


def test_one_count_gives_no_count_based_use():
    d1 = date(2026, 11, 1)
    moves = [_move(d1, "count", "count_variance", "50", 1, "count")]
    assert count_based_use(moves, observations(D(0), moves, [])) is None


def test_recorded_use_while_sales_are_complete_else_counts_and_say_so():
    d1, d8 = date(2026, 11, 1), date(2026, 11, 8)
    counts = count_based_use(
        [],
        [
            Observation(datetime(2026, 11, 1, tzinfo=UTC), d1, D(80)),
            Observation(datetime(2026, 11, 8, tzinfo=UTC), d8, D(10)),
        ],
    )
    complete = daily_use(D(280), 28, True, counts)
    assert (complete.basis, complete.per_day) == ("recorded", D(10))
    incomplete = daily_use(D(28), 28, False, counts)
    assert (incomplete.basis, incomplete.per_day) == ("counts", D(10))
    assert "sales are incomplete" in incomplete.note
    guess = daily_use(D(28), 28, False, None)
    assert guess.basis == "recorded" and "may be low" in guess.note
    assert daily_use(D(0), 28, True, None).basis == "none"


def test_ac_ror_03_count_first():
    assert "below zero" in count_first(D(-1), NOW, NOW)
    assert "Never counted" in count_first(D(5), None, NOW)
    assert "20 days ago" in count_first(D(5), NOW - timedelta(days=20), NOW)
    assert count_first(D(5), NOW - timedelta(days=3), NOW) is None


def test_order_by_lands_a_delivery_on_a_delivery_day():
    today = date(2026, 11, 19)  # a Thursday
    inputs = Inputs(D(1), 2, 7, D(0), D(10), D(20))  # 10 days to the reorder point
    assert order_by(today, inputs) == date(2026, 11, 27)
    tuesdays = 1 << 1  # a delivery on the 29th (Sunday) moves back to Tuesday the 24th
    assert order_by(today, inputs, tuesdays) == date(2026, 11, 22)
    below = Inputs(D(1), 2, 7, D(0), D(10), D(5))
    assert order_by(today, below, tuesdays) == today


# --- on the ledger --------------------------------------------------------------------------------


@pytest.fixture
def shop(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    flour = create_item(
        db,
        org,
        NewItem(
            name="Flour",
            location_ids=[location.id],
            dimension="weight",
            display_unit="g",
            quantity="5000",
            reorder_point="3000",
        ),
    )
    mill = create_supplier(db, org, SupplierFields(name="Mill Co", lead_time_days="2"))
    add_supplier_item(
        db,
        org,
        mill.id,
        inventory_item_id=flour.id,
        purchase_unit="sack",
        pack_quantity="5000",
        pack_unit="g",
        preferred=True,
    )
    stock = db.scalars(select(StockItem).where(StockItem.inventory_item_id == flour.id)).one()
    today = business_date(NOW, location.timezone, location.business_day_cutoff_hour)
    db.commit()
    return org, stock, today


def _history(db, stock, today, first_count=None):
    """Four weeks of 2 kg a day sold, in time order; a count today finds 5 kg, and with
    first_count an earlier count two weeks back."""
    for back in range(27, -1, -1):
        day = today - timedelta(days=back)
        if first_count is not None and back == 13:
            counted_to(db, stock, first_count, at=datetime.combine(day, datetime.min.time(), UTC))
        consumed_on(db, stock, day, "2000")
    counted_to(db, stock, 5000, at=NOW - timedelta(hours=1))
    stock.last_counted_at = NOW - timedelta(hours=1)
    db.commit()


def test_suggestions_come_from_recorded_use_while_sales_are_complete(db, shop, location):
    org, stock, today = shop
    add_source(
        db, org, location_id=location.id, kind="pos_adapter", active_from=today - timedelta(60)
    )
    _history(db, stock, today)
    [group] = plan(db, org, cover_days=3, safety_percent=D(15), now=NOW)
    [line] = group.lines
    assert group.supplier == "Mill Co"
    assert line.usage.basis == "recorded" and line.usage.per_day == D(2000)
    assert line.suggestion is not None
    assert (line.suggestion.quantity, line.suggestion.packs) == (D(10000), 2)  # AC-ROR-01
    assert line.purchase_unit == "sack"


def test_without_complete_sales_two_counts_give_the_use(db, shop, location):
    org, stock, today = shop
    _history(db, stock, today, first_count=30000)  # no sales source: sales are incomplete
    [group] = plan(db, org, now=NOW)
    [line] = group.lines
    assert line.usage.basis == "counts"
    assert "sales are incomplete" in line.usage.note


def test_negative_or_never_counted_stock_is_count_first(db, shop, location):
    org, stock, today = shop
    consumed_on(db, stock, today - timedelta(days=1), "9000")
    db.commit()
    [group] = plan(db, org, now=NOW)
    [line] = group.lines
    assert line.suggestion is None and "below zero" in line.count_first


def test_the_page_explains_and_the_csv_is_grouped_by_supplier(db, shop, location):
    from fastapi.testclient import TestClient

    from app.main import app

    _org, stock, today = shop
    _history(db, stock, today)
    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        page = client.get("/app/reorder")
        assert page.status_code == 200
        assert "Mill Co" in page.text and "reorder point" in page.text and "on hand" in page.text
        sheet = client.get("/app/reorder.csv", params={"cover": 3, "safety": "15"})
        assert sheet.status_code == 200
        header, row = sheet.text.splitlines()[:2]
        assert header.startswith("Supplier,Order by,Item")
        assert row.startswith("Mill Co,")
        assert client.get("/app/reorder", params={"window": 5}).status_code == 400


def test_ac_ror_05_nothing_orders_from_the_reorder_screens():
    from app.main import app

    methods = {
        method
        for route in app.routes
        if getattr(route, "path", "").startswith("/app/reorder")
        for method in getattr(route, "methods", set())
    }
    assert methods <= {"GET", "HEAD"}
