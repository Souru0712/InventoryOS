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
