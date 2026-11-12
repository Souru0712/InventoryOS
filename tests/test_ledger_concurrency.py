"""T1.16-b: concurrent postings serialize and reversals stay honest (DR-02, DR-06, DR-07).

The concurrency tests need a database where two sessions really run at once and lock rows:
Postgres (ADR-0018). SQLite locks the whole file, so they skip there; CI runs them on
Postgres 16, where skips are not allowed.
"""

import threading
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.access import OrgContext
from app.db import SessionLocal, is_postgres
from app.ledger.posting import (
    NotFound,
    balance,
    movements_total,
    post_transaction,
    reverse_transaction,
    reversed_by,
)
from app.models import InventoryTransaction, Location, StockItem, Tenant, TrackMode
from app.transform.ledger import LedgerRuleError, Line
from app.units import Dimension

D = Decimal
NOON = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
postgres_only = pytest.mark.skipif(
    not is_postgres(),
    reason="two sessions locking rows at once is a Postgres property (ADR-0018)",
)


def stock(db, location: Location, slug: str) -> StockItem:
    row = StockItem(
        tenant_id=location.tenant_id,
        location_id=location.id,
        slug=slug,
        name=slug.title(),
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.MANUAL,
    )
    db.add(row)
    db.flush()
    return row


def pair(item: StockItem) -> tuple[int, int]:
    return (item.location_id, item.inventory_item_id)


def posting(org, kind, key, *lines):
    """A job for one thread: post and hand back the transaction's id."""

    def job(db):
        txn = post_transaction(
            db,
            org,
            kind=kind,
            source_type="test",
            source_id=key,
            idempotency_key=key,
            lines=list(lines),
            occurred_at=NOON,
        )
        return txn.id

    return job


def together(*jobs):
    """Run each job in its own thread and session, all released at the same moment."""
    start = threading.Barrier(len(jobs))
    results: list = [None] * len(jobs)
    errors: list = []

    def run(index, job):
        try:
            with SessionLocal() as db:
                start.wait(timeout=10)
                results[index] = job(db)
                db.commit()
        except Exception as exc:  # noqa: BLE001 (reported by the assertion below)
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i, job)) for i, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    return results


def transactions() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count(InventoryTransaction.id))) or 0)


def recorded(org, item) -> tuple[Decimal, Decimal]:
    with SessionLocal() as db:
        return balance(db, org, *pair(item)), movements_total(db, org, *pair(item))


@pytest.fixture
def shop(db, tenant, location):
    milk, oats = stock(db, location, "milk"), stock(db, location, "oats")
    db.commit()
    return OrgContext.system(tenant.id), milk, oats


# --- AC-INV-07 and DR-07 (Postgres) -------------------------------------------------------------


@postgres_only
def test_ac_inv_07_a_receipt_and_waste_at_the_same_moment_both_count(shop):
    org, milk, _ = shop
    expected = D(0)
    for trial in range(50):
        received, wasted = D(1000 + trial), D(-(10 + trial))
        together(
            posting(org, "receipt", f"{trial}:r", Line(*pair(milk), "receipt", received)),
            posting(org, "waste", f"{trial}:w", Line(*pair(milk), "waste", wasted)),
        )
        expected += received + wasted
        assert recorded(org, milk) == (expected, expected)
    assert transactions() == 100


@postgres_only
def test_the_same_key_posted_twice_at_once_posts_once(shop):
    org, milk, _ = shop
    for trial in range(20):
        line = Line(*pair(milk), "receipt", D(5))
        first, second = together(
            posting(org, "receipt", f"same:{trial}", line),
            posting(org, "receipt", f"same:{trial}", line),
        )
        assert first == second
    assert transactions() == 20
    assert recorded(org, milk) == (D(100), D(100))


@postgres_only
def test_postings_naming_items_in_opposite_orders_do_not_deadlock(shop):
    org, milk, oats = shop
    for trial in range(20):
        together(
            posting(
                org,
                "production",
                f"{trial}:a",
                Line(*pair(milk), "production_input", D(-5)),
                Line(*pair(oats), "production_output", D(5)),
            ),
            posting(
                org,
                "production",
                f"{trial}:b",
                Line(*pair(oats), "production_input", D(-3)),
                Line(*pair(milk), "production_output", D(3)),
            ),
        )
    assert recorded(org, milk) == (D(-40), D(-40))
    assert recorded(org, oats) == (D(40), D(40))


@postgres_only
def test_two_first_postings_to_a_new_item_both_land(db, shop, location):
    org, _, _ = shop
    for trial in range(20):
        fresh = stock(db, location, f"new-{trial}")
        db.commit()
        together(
            posting(org, "receipt", f"{trial}:1", Line(*pair(fresh), "receipt", D(2))),
            posting(org, "receipt", f"{trial}:2", Line(*pair(fresh), "receipt", D(3))),
        )
        assert recorded(org, fresh) == (D(5), D(5))


@postgres_only
def test_a_posting_reversed_twice_at_once_is_reversed_once(db, shop):
    org, milk, _ = shop
    for trial in range(10):
        original = post_transaction(
            db,
            org,
            kind="receipt",
            source_type="test",
            source_id=str(trial),
            idempotency_key=f"orig:{trial}",
            lines=[Line(*pair(milk), "receipt", D(7))],
            occurred_at=NOON,
        ).id
        db.commit()

        def undo(session, txn=original):
            return reverse_transaction(session, org, txn, reason="twice").id

        first, second = together(undo, undo)
        assert first == second
    assert recorded(org, milk) == (D(0), D(0))
    assert transactions() == 20


# --- reversal rules (both databases) ------------------------------------------------------------


def test_only_a_reversal_names_what_it_undoes(db, shop):
    org, milk, _ = shop
    with pytest.raises(LedgerRuleError, match="only a reversal"):
        post_transaction(
            db,
            org,
            kind="reversal",
            source_type="t",
            source_id="1",
            idempotency_key="r",
            lines=[Line(*pair(milk), "reversal", D(-1), reverses_movement_id=1)],
            occurred_at=NOON,
        )
    with pytest.raises(LedgerRuleError, match="nothing else names one"):
        post_transaction(
            db,
            org,
            kind="receipt",
            source_type="t",
            source_id="1",
            idempotency_key="x",
            lines=[Line(*pair(milk), "receipt", D(1), reverses_movement_id=1)],
            occurred_at=NOON,
        )


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO inventory_transactions (tenant_id, location_id, kind, source_type,"
        " source_id, revision, idempotency_key, occurred_at, recorded_at) VALUES (:t, :l,"
        " 'reversal', 's', '1', 0, 'k', :at, :at)",
        "INSERT INTO inventory_movements (tenant_id, transaction_id, location_id,"
        " inventory_item_id, allocation_mode, movement_type, qty_base, unit_cost_snapshot,"
        " occurred_at, recorded_at) VALUES (:t, :x, :l, :i, 'none', 'reversal', '-1.0000',"
        " '0', :at, :at)",
    ],
)
def test_the_database_refuses_a_reversal_that_names_nothing(db, shop, statement):
    org, milk, _ = shop
    receipt = post_transaction(
        db,
        org,
        kind="receipt",
        source_type="t",
        source_id="1",
        idempotency_key="rcpt",
        lines=[Line(*pair(milk), "receipt", D(1))],
        occurred_at=NOON,
    )
    db.commit()
    values = {
        "t": org.tenant_id,
        "l": milk.location_id,
        "i": milk.inventory_item_id,
        "x": receipt.id,
        "at": NOON,
    }
    with pytest.raises(IntegrityError):
        db.execute(text(statement), values)
        db.flush()
    db.rollback()


def test_a_corrected_posting_can_find_its_reversal(db, shop):
    org, milk, _ = shop
    receipt = post_transaction(
        db,
        org,
        kind="receipt",
        source_type="receiving",
        source_id="42",
        idempotency_key="rc",
        lines=[Line(*pair(milk), "receipt", D(4))],
        occurred_at=NOON,
    )
    assert reversed_by(db, org, receipt.id) is None
    undo = reverse_transaction(db, org, receipt.id, reason="wrong supplier")
    assert reversed_by(db, org, receipt.id).id == undo.id
    assert (undo.source_type, undo.source_id, undo.revision) == ("receiving", "42", 1)


def test_another_business_cannot_reverse_a_posting(db, shop):
    org, milk, _ = shop
    receipt = post_transaction(
        db,
        org,
        kind="receipt",
        source_type="t",
        source_id="1",
        idempotency_key="mine",
        lines=[Line(*pair(milk), "receipt", D(4))],
        occurred_at=NOON,
    )
    other = Tenant(name="Juniper")
    db.add(other)
    db.commit()
    with pytest.raises(NotFound):
        reverse_transaction(db, OrgContext.system(other.id), receipt.id, reason="not theirs")
