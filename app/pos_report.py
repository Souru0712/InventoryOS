"""What has reached InventoryOS from a business's POS, as plain lines for a run log (T3.11).

Read only. Each line names what the sandbox run (docs/roadmap/SQUARE_SANDBOX_RUN.md) checks:
the connection and its health, which locations track the POS's, the catalog with its units and
categories, the mappings, the orders and the sales they became, refunds, open exceptions, the
latest sync runs and webhook events.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.connections import current, name_of
from app.mappings import modifier_rows, product_rows
from app.models import (
    ExceptionRecord,
    ExternalOrder,
    ExternalProduct,
    Location,
    SalesSource,
    SalesTransaction,
    SyncRun,
    WebhookEvent,
)
from app.order_ingestion import order_summary
from app.pos_catalog import summary
from app.sales import sales_money
from app.sales_status import connection_health
from app.transform.exceptions import OPEN_STATUSES


def _tally(values) -> str:
    counts = Counter(values)
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=str)) or "none"


def pos_report(
    db: Session, org: OrgContext, provider: str = "square", now: datetime | None = None
) -> list[str]:
    now = now or datetime.now(UTC)
    name = name_of(provider)
    connection = current(db, org, provider)
    if connection is None:
        return [f"{name}: never connected"]
    health = connection_health(db, org, connection, now)
    catalog = summary(db, org, connection)
    orders = order_summary(db, org, connection)
    lines = [
        f"connection: {connection.status}, merchant {connection.merchant_id}, health {health.state}"
        + (
            f", token renewed before {connection.expires_at:%Y-%m-%d}"
            if connection.expires_at
            else ""
        ),
        f"reads: last success {health.reads.last_success or '-'}, failed {health.reads.failures_24h}"
        f" of {health.reads.runs_24h} in 24 h, webhook events waiting {health.waiting_events}",
    ]
    for place in db.scalars(
        select_scoped(org, Location).where(Location.provider == provider).order_by(Location.name)
    ):
        lines.append(f"location: {place.name} tracks {name} {place.provider_location_id}")
    externals = list(
        db.scalars(
            select_scoped(org, ExternalProduct).where(
                ExternalProduct.connection_id == connection.id
            )
        )
    )
    lines += [
        f"catalog: {catalog.items} items, {catalog.variations} variations, {catalog.modifiers}"
        f" modifiers; {catalog.locations} {name} locations, {catalog.tracked} tracked",
        f"units: {_tally(p.unit or '-' for p in externals)}",
        f"categories: {_tally(p.category or '(none)' for p in externals)}",
    ]
    rows = product_rows(db, org, connection)
    lines.append(
        f"mappings: {sum(1 for r in rows if r.mapped)} of {len(rows)} products mapped; "
        f"{sum(1 for _e, m in modifier_rows(db, org, connection) if m)} modifiers mapped"
    )
    staged = list(
        db.scalars(
            select_scoped(org, ExternalOrder).where(ExternalOrder.connection_id == connection.id)
        )
    )
    lines += [
        f"orders: {_tally(o.state for o in staged)}; {orders.sales} became sales; order lines from"
        f" {orders.lines_from or '-'}, read up to {orders.through or '-'}",
        f"refunds: {orders.refunds} recorded, {orders.unmatched_refunds} not matched to an order",
    ]
    sales = list(
        db.scalars(
            select_scoped(org, SalesTransaction)
            .join(SalesSource, SalesSource.id == SalesTransaction.sales_source_id)
            .where(SalesSource.provider == provider)
        )
    )
    lines.append(
        f"sales: {_tally(s.status for s in sales)}; lines "
        f"{_tally(ln.consumption_status for s in sales for ln in s.lines)}"
    )
    exceptions = db.scalars(
        select_scoped(org, ExceptionRecord).where(ExceptionRecord.status.in_(OPEN_STATUSES))
    )
    lines.append(f"open exceptions: {_tally(e.type for e in exceptions)}")
    # A window wide enough for every location's business days this past week.
    money = sales_money(db, org, (now - timedelta(days=8)).date(), (now + timedelta(days=1)).date())
    lines.append(
        f"money (last 7 days): sales {money.gross_cents / 100:.2f}, refunded "
        f"{money.refunded_cents / 100:.2f}, net {money.net_cents / 100:.2f}"
    )
    for run in db.scalars(
        select_scoped(org, SyncRun).order_by(SyncRun.started_at.desc()).limit(10)
    ):
        lines.append(
            f"run: {run.started_at:%m-%d %H:%M} {run.kind} {run.status.value}"
            + (f" - {run.error[:120]}" if run.error else "")
        )
    # Events belong to no organization until their merchant names one: read by the business's id.
    events = db.scalars(select(WebhookEvent).where(WebhookEvent.tenant_id == org.tenant_id))
    lines.append(f"webhook events: {_tally(f'{e.event_type} {e.status}' for e in events)}")
    return lines
