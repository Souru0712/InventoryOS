"""POS order-line ingestion (T3.05, ADR-0079; POS-FR-05, POS-FR-07, SQ-10..13, SQ-18, PD-16).

The only writer of `order_cursors`, `external_orders` and `external_order_lines`.
Provider-neutral: each active connection's adapter is asked for the orders changed since its
cursor, less an overlap so late updates are seen again (SQ-11). Every order is staged as the POS
last told it, and a repeated or older version changes nothing.

A completed order at a tracked location becomes one sale through `record_pos_order()`, a line per
order line keyed by its uid (SQ-10), so a resend or a new version is a revision, never a second
sale. Its consumption follows as a job (WF-12, PD-16: Square consumes on completion). Open and
cancelled orders are staged only (SQ-13). Custom amounts are no sale line (SQ-18). An unmapped
modifier is an exception.

On a connection's first read the cursor is set seven days back. From that business date on,
order lines own the location's POS sales, and the legacy daily totals there are voided: the same
orders, counted once.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.connections import ACTIVE, credentials, mark_revoked, name_of
from app.exceptions import apply_findings, clear, record_exception
from app.models import (
    ExternalOrder,
    ExternalOrderLine,
    Location,
    OrderCursor,
    ProviderConnection,
    SalesReturn,
    SalesSource,
    SalesTransaction,
    SyncRun,
    SyncStatus,
)
from app.providers import adapter_for
from app.providers.base import AdapterError, NormalizedOrder
from app.sales import (
    mapped_modifiers,
    mapped_variations,
    record_pos_order,
    record_pos_refund,
    retire_daily_totals,
    void_sale,
)
from app.transform.business_days import business_date, business_day_end, business_day_start
from app.transform.exceptions import Finding
from app.transform.sales import SaleLine

log = logging.getLogger(__name__)

BACKFILL_DAYS = 7
OVERLAP = timedelta(minutes=10)
REREAD_EVERY = timedelta(hours=1)
REREAD_WINDOW = timedelta(hours=2)
RECONCILE_DAYS = 3  # closed business days checked each time, so a fixed day is seen to match
DAILY_RETIRED = "Replaced by the POS's order lines (T3.05)"
COMPLETED, CANCELED = "completed", "canceled"
VOIDED = "Voided at the POS after it was completed"


@dataclass(frozen=True)
class OrderSummary:
    """What the connection's order reads have brought in (the import summary)."""

    orders: int
    sales: int
    lines_from: date | None
    through: datetime | None
    refunds: int = 0
    unmatched_refunds: int = 0  # their order has not arrived (or a daily-total day owns it)
    refunded_cents: int = 0


def _tracked(db: Session, org: OrgContext, provider: str) -> dict[str, Location]:
    return {
        place.provider_location_id: place
        for place in db.scalars(
            select_scoped(org, Location).where(
                Location.provider == provider,
                Location.provider_location_id.is_not(None),
                Location.is_active.is_(True),
                Location.archived_at.is_(None),
            )
        )
        if place.provider_location_id
    }


def _cursor(
    db: Session,
    org: OrgContext,
    connection: ProviderConnection,
    tracked: dict[str, Location],
    now: datetime,
) -> OrderCursor:
    existing = db.scalars(
        select_scoped(org, OrderCursor).where(OrderCursor.connection_id == connection.id)
    ).first()
    if existing is not None:
        return existing
    today = max(
        business_date(now, p.timezone, p.business_day_cutoff_hour) for p in tracked.values()
    )
    lines_from = today - timedelta(days=BACKFILL_DAYS)
    cursor = OrderCursor(
        tenant_id=org.tenant_id,
        connection_id=connection.id,
        lines_from=lines_from,
        # Read from a little before, so every order of the first business day is in.
        through=now - timedelta(days=BACKFILL_DAYS + 2),
        updated_at=now,
    )
    db.add(cursor)
    db.flush()
    retired = retire_daily_totals(db, org, connection.provider, lines_from, DAILY_RETIRED)
    log.info(
        "order lines own %s sales from %s for tenant %s; %d daily totals voided",
        connection.provider,
        lines_from,
        org.tenant_id,
        retired,
    )
    return cursor


def _stage(
    db: Session,
    org: OrgContext,
    connection: ProviderConnection,
    order: NormalizedOrder,
    place: Location | None,
) -> tuple[ExternalOrder, bool]:
    """The order as the POS last told it; False when this version was already staged."""
    row = db.scalars(
        select_scoped(org, ExternalOrder).where(
            ExternalOrder.connection_id == connection.id,
            ExternalOrder.external_order_id == order.external_order_id,
        )
    ).first()
    if row is not None and row.version >= order.version:
        return row, False
    if row is None:
        row = ExternalOrder(
            tenant_id=org.tenant_id,
            connection_id=connection.id,
            external_order_id=order.external_order_id,
        )
        db.add(row)
    row.version = order.version
    row.state = order.state
    row.occurred_at = order.occurred_at
    row.updated_at = order.updated_at
    row.external_location_id = order.external_location_id
    row.location_id = place.id if place else None
    row.lines.clear()
    db.flush()  # the old lines go before the new take their uids
    for index, line in enumerate(order.lines):
        row.lines.append(
            ExternalOrderLine(
                tenant_id=org.tenant_id,
                line_uid=line.line_uid or f"#{index}",
                external_variation_id=line.external_variation_id,
                name=line.name[:300],
                quantity=line.qty,
                modifiers=json.dumps(
                    [[m.external_modifier_id, m.name, str(m.qty)] for m in line.modifiers]
                ),
                gross_cents=line.gross_cents,
                net_cents=line.net_cents,
            )
        )
    db.flush()
    return row, True


def _sale_lines(
    db: Session,
    org: OrgContext,
    order: NormalizedOrder,
    place: Location,
    variations: dict[str, int],
    modifiers: dict[str, int],
) -> list[SaleLine]:
    """One sale line per order line naming a POS item, its mapped modifiers with it."""
    lines = []
    for index, line in enumerate(order.lines):
        if not line.external_variation_id:
            continue  # a custom amount ties to no product (SQ-18)
        chosen: dict[int, int] = defaultdict(int)
        for picked in line.modifiers:
            target = modifiers.get(picked.external_modifier_id or "")
            if target is not None:
                chosen[target] += max(1, int(picked.qty))
            elif picked.external_modifier_id:
                record_exception(
                    db,
                    org,
                    Finding(
                        type="unmapped_product",
                        subject_type="pos_modifier",
                        subject_id=picked.external_modifier_id[:80],
                        title=(
                            f"{picked.name or 'A modifier'} was chosen on sales at {place.name} "
                            "but is not linked to one of your modifiers, so it changed nothing."
                        ),
                        location_id=place.id,
                    ),
                )
        lines.append(
            SaleLine(
                f"line:{line.line_uid or index}",
                line.qty,
                variations.get(line.external_variation_id),
                line.external_variation_id,
                line.name,
                tuple(sorted(chosen.items())),
                line.gross_cents,
                line.net_cents,
            )
        )
    return lines


def _refunds_and_voids(
    db: Session,
    org: OrgContext,
    connection: ProviderConnection,
    order: NormalizedOrder,
    row: ExternalOrder,
    place: Location,
    now: datetime,
) -> Counter[str]:
    """WF-17: a completed order voided at the POS gives back what it took; a completed refund is
    a sales return that lowers net sales and never puts stock back (DR-26)."""
    stats: Counter[str] = Counter()
    if order.state == CANCELED and row.sales_transaction_id is not None:
        sale = get_scoped(db, org, SalesTransaction, row.sales_transaction_id)
        if sale is not None and void_sale(db, org, sale, VOIDED):
            stats["voided"] += 1
    for refund in order.refunds:
        if refund.status != COMPLETED:
            continue  # pending, rejected or failed: no money went back yet
        made = record_pos_refund(
            db,
            org,
            connection.provider,
            place.id,
            reference=f"refund:{refund.external_refund_id}",
            original_reference=f"order:{refund.external_order_id}",
            occurred_at=refund.occurred_at or order.updated_at or now,
            amount_cents=refund.amount_cents,
            lines=[(f"line:{line.line_uid}", line.qty) for line in refund.lines],
        )
        stats["refunds"] += made is not None
    return stats


def _take(
    db: Session,
    org: OrgContext,
    connection: ProviderConnection,
    order: NormalizedOrder,
    tracked: dict[str, Location],
    lines_from: date,
    mapped: tuple[dict[str, int], dict[str, int]],
    now: datetime,
) -> Counter[str]:
    """One order, staged and, if it is a completed sale at a tracked location, recorded."""
    stats: Counter[str] = Counter({"fetched": 1})
    place = tracked.get(order.external_location_id)
    row, changed = _stage(db, org, connection, order, place)
    if not changed:
        stats["unchanged"] += 1
        return stats
    if place is not None:
        stats.update(_refunds_and_voids(db, org, connection, order, row, place, now))
    if order.state != COMPLETED or place is None:
        stats["staged"] += 1  # open, cancelled or untracked: no sale (SQ-13)
        return stats
    day = business_date(order.occurred_at, place.timezone, place.business_day_cutoff_hour)
    lines = _sale_lines(db, org, order, place, *mapped)
    if day < lines_from or not lines:
        stats["staged"] += 1  # a day the daily totals own, or nothing countable
        return stats
    sale = record_pos_order(
        db,
        org,
        connection.provider,
        place.id,
        reference=f"order:{order.external_order_id}",
        occurred_at=order.occurred_at,
        lines=lines,
        gross_cents=sum(ln.gross_cents or 0 for ln in lines),
        net_cents=sum(ln.net_cents or 0 for ln in lines),
    )
    if sale is not None:
        row.sales_transaction_id = sale.id
        stats["sales"] += 1
    return stats


def _failure(connection: ProviderConnection, what: str) -> Finding:
    return Finding(
        type="sync_failure",
        subject_type="provider_connection",
        subject_id=str(connection.id),
        title=f"{what} from {name_of(connection.provider)} failed; it is tried again on the "
        "next read",
    )


def _failed(
    db: Session,
    org: OrgContext,
    connection: ProviderConnection,
    run: SyncRun,
    exc: AdapterError,
    what: str,
    now: datetime,
) -> Counter[str]:
    """A read that failed: kept on its sync run and as an exception, never retried blind. A
    revoked authorization is marked (AC-POS-09); anything else waits for the next read."""
    run.status, run.finished_at, run.error = SyncStatus.FAILED, now, str(exc)[:500]
    if exc.revoked:
        mark_revoked(db, org, connection, now=now)
        db.flush()
        return Counter({"revoked": 1})
    record_exception(db, org, _failure(connection, what), now=now)
    db.flush()
    log.warning("%s read failed for connection %s", connection.provider, connection.id)
    return Counter({"failed": 1})


def _ingest_connection(
    db: Session, org: OrgContext, connection: ProviderConnection, now: datetime
) -> Counter[str]:
    stats: Counter[str] = Counter()
    tracked = _tracked(db, org, connection.provider)
    if not tracked:
        stats["untracked"] += 1
        return stats  # no location takes its sales yet: nothing to read
    held = credentials(db, org, connection, now)
    cursor = _cursor(db, org, connection, tracked, now)
    since = cursor.through - OVERLAP
    wide = cursor.reread_at is None or now - cursor.reread_at >= REREAD_EVERY
    if wide:
        # Once an hour the read goes back a whole window: an order Square delivers late with an
        # earlier time (an offline payment, a missed webhook) is still picked up (AC-POS-07).
        since = min(since, now - REREAD_WINDOW)
    run = SyncRun(tenant_id=org.tenant_id, provider=connection.provider, kind="orders")
    db.add(run)
    db.flush()
    try:
        page = adapter_for(connection.provider).fetch_orders(held, sorted(tracked), since, now)
    except AdapterError as exc:
        return stats + _failed(db, org, connection, run, exc, "Reading orders", now)
    mapped = (
        mapped_variations(db, org, connection.provider),
        mapped_modifiers(db, org, connection.provider),
    )
    for order in page.orders:
        stats.update(_take(db, org, connection, order, tracked, cursor.lines_from, mapped, now))
    cursor.through, cursor.updated_at = now, now
    if wide:
        cursor.reread_at = now
        stats["reread"] += 1
    run.status, run.finished_at = SyncStatus.SUCCESS, now
    run.stats_json = json.dumps(dict(stats))
    clear(db, org, _failure(connection, "").key, now)  # the next success clears it
    db.flush()
    log.info("read %s orders for tenant %s: %s", name_of(connection.provider), org.tenant_id, stats)
    return stats


def ingest_orders(db: Session, org: OrgContext, now: datetime | None = None) -> dict[str, int]:
    """Read every active connection's changed orders; safe to repeat (a job, SQ-11/12)."""
    now = now or datetime.now(UTC)
    totals: Counter[str] = Counter()
    for connection in db.scalars(
        select_scoped(org, ProviderConnection).where(ProviderConnection.status == ACTIVE)
    ):
        totals.update(_ingest_connection(db, org, connection, now))
    return dict(totals)


def order_summary(db: Session, org: OrgContext, connection: ProviderConnection) -> OrderSummary:
    cursor = db.scalars(
        select_scoped(org, OrderCursor).where(OrderCursor.connection_id == connection.id)
    ).first()
    orders = db.scalar(
        select_scoped(org, ExternalOrder)
        .with_only_columns(func.count())
        .where(ExternalOrder.connection_id == connection.id)
    )
    sales = db.scalar(
        select_scoped(org, ExternalOrder)
        .with_only_columns(func.count())
        .where(
            ExternalOrder.connection_id == connection.id,
            ExternalOrder.sales_transaction_id.is_not(None),
        )
    )
    returns = list(
        db.scalars(
            select_scoped(org, SalesReturn)
            .join(SalesSource, SalesSource.id == SalesReturn.sales_source_id)
            .where(SalesSource.provider == connection.provider)
        )
    )
    return OrderSummary(
        orders or 0,
        sales or 0,
        cursor.lines_from if cursor else None,
        cursor.through if cursor else None,
        refunds=len(returns),
        unmatched_refunds=len([r for r in returns if r.sales_transaction_id is None]),
        refunded_cents=sum(r.amount_cents or 0 for r in returns),
    )


# --- daily reconciliation (T3.08, SQ-17, AC-POS-08) ------------------------------------------------


def _ours(
    db: Session,
    org: OrgContext,
    connection: ProviderConnection,
    external_location_id: str,
    start: datetime,
    end: datetime,
) -> dict[str, int]:
    """Our staged completed orders at a POS location in a window: order id -> its line count."""
    rows = db.scalars(
        select_scoped(org, ExternalOrder).where(
            ExternalOrder.connection_id == connection.id,
            ExternalOrder.external_location_id == external_location_id,
            ExternalOrder.state == COMPLETED,
            ExternalOrder.occurred_at >= start,
            ExternalOrder.occurred_at < end,
        )
    )
    return {row.external_order_id: len(row.lines) for row in rows}


def _reconcile_connection(
    db: Session,
    org: OrgContext,
    connection: ProviderConnection,
    now: datetime,
    findings: list[Finding],
) -> Counter[str]:
    stats: Counter[str] = Counter()
    tracked = _tracked(db, org, connection.provider)
    cursor = db.scalars(
        select_scoped(org, OrderCursor).where(OrderCursor.connection_id == connection.id)
    ).first()
    if not tracked or cursor is None:
        return stats  # nothing read yet: nothing to compare
    held = credentials(db, org, connection, now)
    run = SyncRun(tenant_id=org.tenant_id, provider=connection.provider, kind="reconciliation")
    db.add(run)
    db.flush()
    adapter = adapter_for(connection.provider)
    mapped = (
        mapped_variations(db, org, connection.provider),
        mapped_modifiers(db, org, connection.provider),
    )
    days = []
    for external_id, place in sorted(tracked.items()):
        today = business_date(now, place.timezone, place.business_day_cutoff_hour)
        for back in range(1, RECONCILE_DAYS + 1):
            day = today - timedelta(days=back)
            if day < cursor.lines_from:
                continue  # the daily totals own it: nothing of ours to compare
            start = business_day_start(day, place.timezone, place.business_day_cutoff_hour)
            end = business_day_end(day, place.timezone, place.business_day_cutoff_hour)
            try:
                page = adapter.completed_orders(held, [external_id], start, end)
            except AdapterError as exc:
                return stats + _failed(db, org, connection, run, exc, "Checking sales", now)
            ours = _ours(db, org, connection, external_id, start, end)
            theirs = (len(page.orders), sum(len(o.lines) for o in page.orders))
            mine = (len(ours), sum(ours.values()))
            days.append({"location": place.id, "day": day.isoformat(), "pos": theirs, "ours": mine})
            stats["days"] += 1
            if theirs == mine:
                continue
            stats["mismatched"] += 1
            missing = [o for o in page.orders if ours.get(o.external_order_id) != len(o.lines)]
            findings.append(
                Finding(
                    type="pos_reconciliation_mismatch",
                    subject_type="location_day",
                    subject_id=f"{place.id}:{day.isoformat()}",
                    title=(
                        f"{name_of(connection.provider)} recorded {theirs[0]} orders "
                        f"({theirs[1]} lines) at {place.name} on {day:%d %b}; InventoryOS had "
                        f"{mine[0]} ({mine[1]}). The {len(missing)} that differed were read "
                        "again; this clears when the next check matches."
                    ),
                    location_id=place.id,
                )
            )
            # Recovery: what the POS has and we lack goes through the same path as any read.
            for order in missing:
                stats.update(
                    _take(db, org, connection, order, tracked, cursor.lines_from, mapped, now)
                )
    run.status, run.finished_at = SyncStatus.SUCCESS, now
    run.stats_json = json.dumps({**dict(stats), "checked": days})
    db.flush()
    return stats


def reconcile_orders(db: Session, org: OrgContext, now: datetime | None = None) -> dict[str, int]:
    """Compare the POS's completed orders and lines for each tracked location's last closed
    business days with ours (SQ-17); a mismatch opens `pos_reconciliation_mismatch` and the
    difference is read again. A day that matches clears its exception (a job)."""
    now = now or datetime.now(UTC)
    totals: Counter[str] = Counter()
    findings: list[Finding] = []
    for connection in db.scalars(
        select_scoped(org, ProviderConnection).where(ProviderConnection.status == ACTIVE)
    ):
        totals.update(_reconcile_connection(db, org, connection, now, findings))
    if not totals["failed"]:
        # A complete check answers for its type: what it no longer finds is resolved.
        apply_findings(db, org, findings, frozenset({"pos_reconciliation_mismatch"}), now)
    return dict(totals)
