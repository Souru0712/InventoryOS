"""Test stock that starts with a quantity starts with the ledger history the product gives it.

Since T1.18 the posting service is the only writer of quantities and keeps stock_items.qty_base
equal to the balance (ADR-0042). A fixture that builds a StockItem with qty_base=... by hand
calls opened() so the balance and movements agree with it, as a new item's opening would.
"""

from datetime import UTC, datetime
from decimal import Decimal

from app.access import OrgContext
from app.ledger.posting import SYSTEM, post_opening
from app.models import StockItem


def opened(db, *items: StockItem) -> None:
    """Post each row's quantity as its opening balance (once; a zero needs none)."""
    db.flush()
    for item in items:
        post_opening(
            db,
            OrgContext.system(item.tenant_id),
            location_id=item.location_id,
            inventory_item_id=item.inventory_item_id,
            qty_base=Decimal(item.qty_base or 0),
            source_type="test",
            source_id=str(item.id),
            occurred_at=datetime.now(UTC),
            actor_user_id=SYSTEM,
        )
    db.flush()


def consumed_on(db, stock: StockItem, day, qty) -> None:
    """What sales took of the stock on a day, as sale consumption posts it: velocity reads use
    from the ledger (T2.15). The item is opened first if it is not yet."""
    from datetime import time
    from uuid import uuid4

    from app.ledger.posting import post_transaction
    from app.transform.ledger import Line

    opened(db, stock)
    key = uuid4().hex
    post_transaction(
        db,
        OrgContext.system(stock.tenant_id),
        kind="sale",
        source_type="test_sale",
        source_id=key,
        idempotency_key=f"test-sale:{key}",
        lines=[
            Line(stock.location_id, stock.inventory_item_id, "sale_consumption", -Decimal(str(qty)))
        ],
        occurred_at=datetime.combine(day, time(12), tzinfo=UTC),
        actor_user_id=SYSTEM,
    )
    db.flush()


def counted_to(db, stock: StockItem, qty, at: datetime | None = None) -> None:
    """A count that finds the shelf holding qty (post them in time order: a count compares
    with the balance as it stands)."""
    from uuid import uuid4

    from app.ledger.posting import post_count

    key = uuid4().hex
    post_count(
        db,
        OrgContext.system(stock.tenant_id),
        location_id=stock.location_id,
        inventory_item_id=stock.inventory_item_id,
        counted=Decimal(str(qty)),
        source_type="test",
        source_id=key,
        idempotency_key=f"count:test:{key}",
        occurred_at=at or datetime.now(UTC),
        actor_user_id=SYSTEM,
    )
    db.flush()
