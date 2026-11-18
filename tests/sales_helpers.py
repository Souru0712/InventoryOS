"""Recording sales in tests as the POS feed does, now that `sales_daily` is frozen (DM-7)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.access import OrgContext
from app.models import SalesSource, SalesTransaction, SoldItem
from app.sales import DayOfSales, record_daily_pos_sales


def sold_on(db, location, sold_item: SoldItem, day: date, units, net_cents: int = 0) -> None:
    """Set one POS item's sales at a location for a day, keeping the day's other items: the day
    is one transaction, so it is re-recorded with this line added or changed."""
    entries: dict[str, DayOfSales] = {}
    current = db.scalars(
        select(SalesTransaction)
        .join(SalesSource, SalesSource.id == SalesTransaction.sales_source_id)
        .where(
            SalesTransaction.location_id == location.id,
            SalesTransaction.source_reference == f"daily:{day.isoformat()}",
            SalesTransaction.status == "completed",
            SalesSource.provider == sold_item.provider,
        )
    ).first()
    if current is not None:
        by_ref = {
            s.provider_item_id: s
            for s in db.scalars(select(SoldItem).where(SoldItem.tenant_id == location.tenant_id))
        }
        for line in current.lines:
            entries[line.external_ref] = DayOfSales(
                location.id,
                day,
                by_ref[line.external_ref],
                Decimal(line.quantity),
                line.gross_cents or 0,
                line.net_cents or 0,
            )
    entries[sold_item.provider_item_id] = DayOfSales(
        location.id, day, sold_item, Decimal(str(units)), 0, net_cents
    )
    record_daily_pos_sales(
        db, OrgContext.system(location.tenant_id), sold_item.provider, list(entries.values())
    )
