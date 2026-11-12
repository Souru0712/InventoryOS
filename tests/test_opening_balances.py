"""T1.17 (DM-6 backfill): the ledger starts equal to what the product shows (DR-03)."""

import argparse
import importlib.util
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.access import OrgContext
from app.cli import cmd_demo
from app.db import Base, SessionLocal, engine
from app.ledger.posting import balance, movements_total, post_transaction
from app.models import InventoryMovement, InventoryTransaction, StockBalance, StockItem
from app.transform.ledger import Line

MIGRATION = Path("migrations/versions/a6eec9216078_opening_balances.py")


def load_backfill():
    spec = importlib.util.spec_from_file_location("opening_balances", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.backfill


@pytest.fixture
def demo():
    """The demo café, with one item counted below zero and one at exactly zero."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    args = argparse.Namespace(
        email="demo@stockpulse.local", password="demopassword", name="Rosewood Coffee", days=7
    )
    assert cmd_demo(args) == 0
    with engine.begin() as connection:
        # The demo now opens its items through the ledger (T1.18); before T1.17 there was none.
        for table in ("stock_balances", "inventory_movements", "inventory_transactions"):
            connection.execute(text(f"DELETE FROM {table}"))  # noqa: S608
    with SessionLocal() as db:
        rows = list(db.scalars(select(StockItem).order_by(StockItem.id)))
        rows[0].qty_base = Decimal("-3.5000")  # sales ran ahead of the last count
        rows[1].qty_base = Decimal("0")
        db.commit()
        return {row.id: (row.qty_base, row.cost_per_base_cents) for row in rows}


def test_every_item_at_every_location_opens_at_what_the_product_shows(demo):
    with engine.begin() as connection:
        posted = load_backfill()(connection)
    with SessionLocal() as db:
        stock = list(db.scalars(select(StockItem)))
        org = OrgContext.system(stock[0].tenant_id)
        nonzero = [row for row in stock if row.qty_base != 0]
        assert posted == len(nonzero)
        for row in stock:
            pair = (row.location_id, row.inventory_item_id)
            shown, _cost = demo[row.id]
            # DR-03: the recorded balance and the sum of movements both equal the old quantity.
            assert balance(db, org, *pair) == shown
            assert movements_total(db, org, *pair) == shown
        for movement in db.scalars(select(InventoryMovement)):
            row = next(
                r
                for r in stock
                if r.inventory_item_id == movement.inventory_item_id
                and r.location_id == movement.location_id
            )
            assert movement.movement_type == "opening_balance"
            assert movement.unit_cost_snapshot == max(demo[row.id][1], Decimal("0"))
        for txn in db.scalars(select(InventoryTransaction)):
            location_item = txn.movements[0]
            assert txn.idempotency_key == (
                f"migration:opening:{location_item.inventory_item_id}:{location_item.location_id}"
            )
            assert (txn.kind, txn.source_type, txn.actor_user_id) == ("opening", "migration", None)
        assert len(db.scalars(select(StockBalance)).all()) == len(stock)  # zero ones too


def test_running_it_again_posts_nothing_more(demo):
    backfill = load_backfill()
    with engine.begin() as connection:
        first = backfill(connection)
    with engine.begin() as connection:
        assert backfill(connection) == 0
    with SessionLocal() as db:
        assert len(db.scalars(select(InventoryTransaction)).all()) == first


def test_the_ledger_carries_on_from_the_opening_balance(demo):
    with engine.begin() as connection:
        load_backfill()(connection)
    with SessionLocal() as db:
        row = next(r for r in db.scalars(select(StockItem)) if r.qty_base > 0)
        org = OrgContext.system(row.tenant_id)
        pair = (row.location_id, row.inventory_item_id)
        opening = balance(db, org, *pair)
        post_transaction(
            db,
            org,
            kind="waste",
            source_type="test",
            source_id="1",
            idempotency_key="after",
            lines=[Line(*pair, "waste", Decimal("-1"))],
            occurred_at=datetime.now(UTC),
        )
        db.commit()
        assert balance(db, org, *pair) == movements_total(db, org, *pair) == opening - 1
