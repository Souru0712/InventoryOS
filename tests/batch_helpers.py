"""Dated stock in tests as the product makes it (T2.06): on the ledger, then dated by a batch.

A batch holds what its movements say (DR-15), so a test batch is made by dating stock the
ledger holds, never by adding a `StockBatch` row on its own.
"""

from datetime import date, datetime
from decimal import Decimal

from app.access import OrgContext
from app.batches import date_stock
from app.models import StockBatch, StockItem
from tests.ledger_helpers import opened


def dated(
    db,
    stock: StockItem,
    qty,
    expires_on: date,
    cost=None,
    at: datetime | None = None,
) -> StockBatch:
    """Date that much of the stock, as a label does. The row's quantity is opened on the
    ledger first if it is not there yet (an opening posts once)."""
    opened(db, stock)
    batch = date_stock(
        db, OrgContext.system(stock.tenant_id), stock, Decimal(str(qty)), expires_on, now=at
    )
    if cost is not None:
        batch.cost_per_base_cents = Decimal(str(cost))
    db.flush()
    return batch
