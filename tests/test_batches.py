"""T2.06-a: batches hold what their movements say (BAT-FR-01, -05, -06, -08; DR-13..15;
AC-BAT-01..04)."""

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.batches import date_stock, undate_batch, undated_stock
from app.items import NewItem, create_item
from app.ledger.posting import (
    balance,
    batch_remaining,
    movements_total,
    post_count,
    post_transaction,
    reverse_transaction,
)
from app.models import (
    ExceptionRecord,
    InventoryItem,
    InventoryMovement,
    StockBatch,
    StockItem,
)
from app.orchestration.alerts import evaluate_and_store
from app.orchestration.scanning import receive_scanned
from app.transform.batches import (
    DEPLETED,
    EXPIRED,
    FEFO,
    FIFO,
    BatchRuleError,
    Held,
    in_order,
    status,
    take,
    undated,
)
from app.transform.ledger import Line

D = Decimal
T0 = datetime(2026, 11, 1, 9, tzinfo=UTC)
TODAY = date.today()


# --- the rules (pure) --------------------------------------------------------------------------


def _held(batch_id, remaining, expires_in, received_day):
    return Held(
        batch_id,
        D(remaining),
        TODAY + timedelta(days=expires_in),
        T0 + timedelta(days=received_day),
    )


A = _held(1, 10, 20, 0)  # received first, expires later
B = _held(2, 10, 5, 1)  # received second, expires sooner


def test_fefo_takes_the_soonest_expiry_and_fifo_the_first_received():
    assert [h.batch_id for h in in_order([A, B], FEFO)] == [2, 1]
    assert [h.batch_id for h in in_order([A, B], FIFO)] == [1, 2]
    with pytest.raises(BatchRuleError):
        in_order([A], "lifo")


def test_taking_spans_batches_and_reports_what_none_held():
    assert take(D(14), [A, B], FEFO) == ([(2, D(10)), (1, D(4))], D(0))
    assert take(D(25), [A, B], FIFO) == ([(1, D(10)), (2, D(10))], D(5))


def test_undated_stock_and_a_batchs_status():
    assert undated(D(23), [A, B]) == D(3)
    assert undated(D(15), [A, B]) == D(0)  # never below zero
    assert status(D(0), TODAY - timedelta(days=1), TODAY) == DEPLETED
    assert status(D(2), TODAY - timedelta(days=1), TODAY) == EXPIRED
    assert status(D(2), TODAY, TODAY) == "active"


# --- through the ledger --------------------------------------------------------------------------


@pytest.fixture
def shelf(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    item = create_item(
        db,
        org,
        NewItem(
            name="Milk",
            location_ids=[location.id],
            dimension="count",
            display_unit="each",
            cost_per_unit="1",
        ),
    )
    stock = db.scalars(select(StockItem).where(StockItem.inventory_item_id == item.id)).one()
    db.commit()
    return org, item, stock


def _receive(db, org, stock, qty, expires_in, at):
    receive_scanned(db, org, stock, D(qty), expires_on=TODAY + timedelta(days=expires_in), now=at)
    return db.scalars(
        select(StockBatch)
        .where(StockBatch.stock_item_id == stock.id)
        .order_by(StockBatch.id.desc())
    ).first()


def _sell(db, org, location, item, qty, key, at=None):
    return post_transaction(
        db,
        org,
        kind="sale",
        source_type="test",
        source_id=key,
        idempotency_key=f"sale:{key}",
        lines=[Line(location.id, item.id, "sale_consumption", -D(qty))],
        occurred_at=at or datetime.now(UTC),
    )


def _held_now(db, org, *batches):
    found = batch_remaining(db, org, [b.id for b in batches])
    return [found.get(b.id, D(0)) for b in batches]


def test_ac_bat_01_a_dated_delivery_is_one_batch_holding_what_arrived(db, shelf):
    org, item, stock = shelf
    batch = _receive(db, org, stock, 12, 7, T0)
    assert _held_now(db, org, batch) == [D(12)]
    [movement] = db.scalars(select(InventoryMovement).where(InventoryMovement.batch_id == batch.id))
    assert (movement.movement_type, movement.allocation_mode) == ("receipt", "explicit")


def test_ac_bat_02_a_sale_takes_by_expiry_or_by_arrival(db, shelf, location):
    org, item, stock = shelf
    first = _receive(db, org, stock, 10, 20, T0)  # received first, expires later
    second = _receive(db, org, stock, 10, 5, T0 + timedelta(days=1))  # expires sooner
    _sell(db, org, location, item, 4, "fefo")
    assert _held_now(db, org, first, second) == [D(10), D(6)]
    db.get(InventoryItem, item.id).batch_order = FIFO
    _sell(db, org, location, item, 4, "fifo")
    assert _held_now(db, org, first, second) == [D(6), D(6)]


def test_ac_bat_03_sales_are_inferred_and_a_chosen_batch_is_explicit(db, shelf, location):
    org, item, stock = shelf
    batch = _receive(db, org, stock, 10, 5, T0)
    sale = _sell(db, org, location, item, 3, "s1")
    assert [(m.batch_id, m.allocation_mode) for m in sale.movements] == [(batch.id, "inferred")]
    waste = post_transaction(
        db,
        org,
        kind="waste",
        source_type="test",
        source_id="w1",
        idempotency_key="waste:w1",
        lines=[Line(location.id, item.id, "waste", D(-2), batch_id=batch.id)],
        occurred_at=datetime.now(UTC),
    )
    assert [(m.batch_id, m.allocation_mode) for m in waste.movements] == [(batch.id, "explicit")]
    assert _held_now(db, org, batch) == [D(5)]


def test_a_sale_beyond_every_batch_leaves_what_none_held_undated(db, shelf, location):
    org, item, stock = shelf
    batch = _receive(db, org, stock, 5, 5, T0)
    sale = _sell(db, org, location, item, 8, "big")
    assert sorted((m.batch_id or 0, m.qty_base) for m in sale.movements) == [
        (0, D(-3)),
        (batch.id, D(-5)),
    ]
    assert _held_now(db, org, batch) == [D(0)]
    assert balance(db, org, location.id, item.id) == D(-3)
    assert undated_stock(db, org, stock) == D(0)


def test_a_reversal_gives_stock_back_to_the_batch_it_came_from(db, shelf, location):
    org, item, stock = shelf
    batch = _receive(db, org, stock, 10, 5, T0)
    sale = _sell(db, org, location, item, 4, "undo")
    reverse_transaction(db, org, sale.id, reason="Entered twice")
    assert _held_now(db, org, batch) == [D(10)]


def test_dating_stock_on_the_shelf_and_removing_a_wrong_date_keep_every_batch(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    item = create_item(
        db,
        org,
        NewItem(
            name="Eggs",
            location_ids=[location.id],
            dimension="count",
            display_unit="each",
            quantity="20",
        ),
    )
    stock = db.scalars(select(StockItem).where(StockItem.inventory_item_id == item.id)).one()
    batch = date_stock(db, org, stock, D(8), TODAY + timedelta(days=3))
    assert _held_now(db, org, batch) == [D(8)]
    assert (balance(db, org, location.id, item.id), undated_stock(db, org, stock)) == (D(20), D(12))
    with pytest.raises(BatchRuleError, match="more than the stock without a date"):
        date_stock(db, org, stock, D(13), TODAY)
    # Probe R6, retired: a removed date moves the stock back to undated; the batch stays.
    undate_batch(db, org, batch.id)
    undate_batch(db, org, batch.id)  # again: nothing left to move
    db.commit()
    assert db.get(StockBatch, batch.id) is not None
    assert _held_now(db, org, batch) == [D(0)]
    assert (balance(db, org, location.id, item.id), undated_stock(db, org, stock)) == (D(20), D(20))


def test_an_expired_batch_stays_until_a_count_accounts_for_it(db, shelf, location):
    """DR-14, AC-BAT-05: past its date with stock left, it stays and is an exception; once a
    count finds it gone, the exception resolves by itself."""
    org, item, stock = shelf
    batch = _receive(db, org, stock, 6, -1, T0)
    db.commit()
    for _ in range(2):
        evaluate_and_store(db, org)
        db.commit()
    expired = db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.type == "batch_expired_unresolved")
    ).one()
    assert expired.status == "open" and _held_now(db, org, batch) == [D(6)]
    post_count(
        db,
        org,
        location_id=location.id,
        inventory_item_id=item.id,
        counted=D(0),
        source_type="test",
        source_id="binned",
        idempotency_key="count:binned",
        occurred_at=datetime.now(UTC),
    )
    evaluate_and_store(db, org)
    db.commit()
    db.expire_all()
    assert _held_now(db, org, batch) == [D(0)]
    assert expired.status == "resolved" and expired.resolution == "system"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_conservation_holds_after_any_sequence(db, shelf, location, seed):
    """AC-BAT-04 as a property: no batch ever holds less than nothing; the stock row equals the
    movements; stock leaving without a batch chosen never leaves undated while a batch holds
    stock; and for stock that only ever arrives dated, nothing is undated until a sale takes
    more than every batch held (a shortfall, DR-13)."""
    org, item, stock = shelf
    rng = random.Random(seed)  # noqa: S311 (a repeatable test sequence, not a secret)
    sales: list[int] = []
    batches: list[StockBatch] = []
    short = False
    for step in range(60):
        kind = rng.choice(["receive", "receive", "sell", "sell", "count", "undo"])
        at = T0 + timedelta(hours=step)
        if kind == "receive":
            batches.append(_receive(db, org, stock, rng.randint(1, 10), rng.randint(-2, 20), at))
        elif kind == "sell":
            sale = _sell(db, org, location, item, rng.randint(1, 8), f"{seed}-{step}", at)
            sales.append(sale.id)
            unbatched = sum((-m.qty_base for m in sale.movements if m.batch_id is None), D(0))
            if unbatched > 0:
                short = True
                assert all(q == 0 for q in _held_now(db, org, *batches))
        elif kind == "count":
            held = balance(db, org, location.id, item.id)
            if held > 0:  # a count that finds less: what is missing leaves the batches
                post_count(
                    db,
                    org,
                    location_id=location.id,
                    inventory_item_id=item.id,
                    counted=D(rng.randint(0, int(held))),
                    source_type="test",
                    source_id=f"c{step}",
                    idempotency_key=f"count:{seed}:{step}",
                    occurred_at=at,
                )
        elif sales:
            reverse_transaction(db, org, sales.pop(rng.randrange(len(sales))), reason="Mistake")
        remaining = _held_now(db, org, *batches)
        assert all(q >= 0 for q in remaining)
        total = movements_total(db, org, location.id, item.id)
        db.refresh(stock)
        assert stock.qty_base == balance(db, org, location.id, item.id) == total
        undated_total = total - sum(remaining, D(0))
        assert undated_total == 0 or (short and undated_total < 0)


def test_removing_a_date_through_the_page_keeps_the_batch(db, shelf, location):
    """Probe R6, retired: the page that removes a wrong date no longer deletes the batch."""
    org, item, stock = shelf
    batch = _receive(db, org, stock, 5, 30, T0)
    db.commit()
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        removed = client.post(f"/app/expiring/{batch.id}/remove", follow_redirects=False)
        assert removed.status_code == 303
    db.expire_all()
    assert db.get(StockBatch, batch.id) is not None
    assert _held_now(db, org, batch) == [D(0)]
    assert balance(db, org, location.id, item.id) == D(5)  # the stock itself is unchanged
