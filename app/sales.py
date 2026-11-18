"""Sales: sources, transactions and returns (T2.04; POS-FR-17, -18, DR-29..31, DR-34, ADR-0053).

The only writer of the sales tables. Every sale, from any source, is recorded here as a
transaction (DR-30), and everything that reads sales (velocity, reorder, reports, completeness)
reads it through `daily_sales()`, never a source's own records. Each location has one source of
record per business date (DR-31): a sale from any other source is kept, flagged
`sales_source_overlap` and never counted. A changed resend is the next revision.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.audit import record_audit
from app.exceptions import record_exception
from app.models import (
    Location,
    ProductVariation,
    SalesLineModifier,
    SalesSource,
    SalesTransaction,
    SalesTransactionLine,
    SoldItem,
)
from app.transform.business_days import business_date
from app.transform.exceptions import Finding
from app.transform.sales import (
    ORIGIN_LABELS,
    SALES_ORIGINS,
    SaleLine,
    SalesRuleError,
    Span,
    check_lines,
    check_new_source,
    check_quantity,
    check_span,
    of_record,
    same_sale,
)

log = logging.getLogger(__name__)


class NotFound(LookupError):
    """No such location or source that org may see."""


# --- sources (POS-FR-18, DR-31) -----------------------------------------------------------------


def sources(db: Session, org: OrgContext, location_id: int) -> list[SalesSource]:
    rows = db.scalars(select_scoped(org, SalesSource).where(SalesSource.location_id == location_id))
    return sorted(rows, key=lambda s: (s.active_from, s.id))


def _span(source: SalesSource) -> Span:
    return Span(source.active_from, source.active_to)


def add_source(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    kind: str,
    active_from: date,
    active_to: date | None = None,
    provider: str | None = None,
    of_record: bool = True,
    ip: str | None = None,
) -> SalesSource:
    """A sales source for a location. A source of record may not overlap another (DR-31)."""
    location = get_scoped(db, org, Location, location_id)
    if location is None:
        raise NotFound(location_id)
    if kind not in SALES_ORIGINS:
        raise SalesRuleError("Choose where the sales come from.")
    span = check_span(active_from, active_to)
    if of_record:
        check_new_source(span, (_span(s) for s in sources(db, org, location_id) if s.of_record))
    source = SalesSource(
        tenant_id=org.tenant_id,
        location_id=location_id,
        kind=kind,
        provider=provider,
        active_from=active_from,
        active_to=active_to,
        of_record=of_record,
    )
    db.add(source)
    db.flush()
    record_audit(
        db,
        org,
        "settings.sales_source_added",
        f"{location.name} takes its sales from {ORIGIN_LABELS[kind].lower()}"
        f"{f' ({provider})' if provider else ''} from {active_from:%d %b %Y}"
        + ("" if of_record else ", kept but not counted"),
        subject_type="sales_source",
        subject_id=source.id,
        after={
            "kind": kind,
            "provider": provider,
            "from": str(active_from),
            "of_record": of_record,
        },
        ip=ip,
    )
    return source


def stop_source(
    db: Session, org: OrgContext, source_id: int, last_day: date, ip: str | None = None
) -> SalesSource:
    """A source's last business day, after which another can become the source of record."""
    source = get_scoped(db, org, SalesSource, source_id)
    if source is None:
        raise NotFound(source_id)
    check_span(source.active_from, last_day)
    source.active_to = last_day
    db.flush()
    record_audit(
        db,
        org,
        "settings.sales_source_stopped",
        f"A sales source stops after {last_day:%d %b %Y}",
        subject_type="sales_source",
        subject_id=source.id,
        after={"to": str(last_day)},
        ip=ip,
    )
    return source


def source_of_record(
    db: Session, org: OrgContext, location_id: int, day: date
) -> SalesSource | None:
    """The source whose sales count for a location on a business date (DR-31)."""
    rows = [s for s in sources(db, org, location_id) if s.of_record]
    found = of_record(((s.id, _span(s)) for s in rows), day)
    return next((s for s in rows if s.id == found), None)


# --- transactions (POS-FR-17, DR-30, DR-34) -----------------------------------------------------


def _latest(db: Session, org: OrgContext, source: SalesSource, reference: str):
    rows = db.scalars(
        select_scoped(org, SalesTransaction).where(
            SalesTransaction.sales_source_id == source.id,
            SalesTransaction.source_reference == reference,
        )
    )
    return max(rows, key=lambda t: t.revision, default=None)


def _lines_of(transaction: SalesTransaction) -> list[SaleLine]:
    return [
        SaleLine(
            ln.line_key,
            Decimal(ln.quantity),
            ln.product_variation_id,
            ln.external_ref,
            ln.description,
            tuple((m.modifier_id, m.quantity) for m in ln.modifiers),
            ln.gross_cents,
            ln.net_cents,
        )
        for ln in transaction.lines
    ]


def record_sale(
    db: Session,
    org: OrgContext,
    source: SalesSource,
    *,
    source_reference: str,
    lines: Sequence[SaleLine],
    occurred_at: datetime | None = None,
    day: date | None = None,
    gross_cents: int | None = None,
    discount_cents: int | None = None,
    net_cents: int | None = None,
    tax_cents: int | None = None,
) -> SalesTransaction | None:
    """Record a sale (or a day's sales) from a source; None when a resend changes nothing.

    Its business date is its location's for `occurred_at` (ADR-0027), or the day given for a
    daily entry. A sale from a source that is not the source of record that day is kept,
    flagged `sales_source_overlap` and never counted (DR-31)."""
    location = get_scoped(db, org, Location, source.location_id)
    if location is None:
        raise NotFound(source.location_id)
    if occurred_at is not None:
        day = business_date(occurred_at, location.timezone, location.business_day_cutoff_hour)
    if day is None:
        raise SalesRuleError("A sale needs a time or a business date.")
    checked = check_lines(
        [
            SaleLine(
                ln.line_key,
                check_quantity(ln.quantity),
                ln.product_variation_id,
                ln.external_ref,
                ln.description,
                ln.modifiers,
                ln.gross_cents,
                ln.net_cents,
            )
            for ln in lines
        ]
    )
    money = (gross_cents, discount_cents, net_cents, tax_cents)
    latest = _latest(db, org, source, source_reference)
    if latest is not None and latest.status == "completed":
        unchanged = (
            latest.gross_cents,
            latest.discount_cents,
            latest.net_cents,
            latest.tax_cents,
        ) == money and same_sale(_lines_of(latest), checked)
        if unchanged and latest.business_date == day:
            return None
        latest.status = "corrected"
    record = source_of_record(db, org, location.id, day)
    counted = source.of_record and record is not None and record.id == source.id
    transaction = SalesTransaction(
        tenant_id=org.tenant_id,
        location_id=location.id,
        sales_source_id=source.id,
        origin=source.kind,  # DR-34: from the source, never changed
        source_reference=source_reference,
        revision=(latest.revision + 1) if latest else 1,
        occurred_at=occurred_at,
        business_date=day,
        of_record=counted,
        gross_cents=gross_cents,
        discount_cents=discount_cents,
        net_cents=net_cents,
        tax_cents=tax_cents,
    )
    db.add(transaction)
    db.flush()
    for line in checked:
        row = SalesTransactionLine(
            tenant_id=org.tenant_id,
            sales_transaction_id=transaction.id,
            line_key=line.line_key[:120],
            product_variation_id=line.product_variation_id,
            external_ref=line.external_ref,
            description=line.description[:300],
            quantity=line.quantity,
            gross_cents=line.gross_cents,
            net_cents=line.net_cents,
            consumption_status="pending" if counted else "skipped",
        )
        db.add(row)
        db.flush()
        for modifier_id, how_many in line.modifiers:
            db.add(
                SalesLineModifier(
                    tenant_id=org.tenant_id,
                    sales_transaction_id=transaction.id,
                    sales_transaction_line_id=row.id,
                    modifier_id=modifier_id,
                    quantity=how_many,
                )
            )
    db.flush()
    if not counted:
        record_exception(
            db,
            org,
            Finding(
                type="sales_source_overlap",
                subject_type="location_day",
                subject_id=f"{location.id}:{day.isoformat()}",
                title=(
                    f"{location.name} received sales for {day:%d %b %Y} from "
                    f"{ORIGIN_LABELS[source.kind].lower()}, which is not its source of record "
                    "that day. They are kept but not counted."
                ),
                location_id=location.id,
            ),
        )
    return transaction


# --- reading (DR-30) -----------------------------------------------------------------------------


@dataclass(frozen=True)
class DailySale:
    """What was sold of one thing at one location on one business day, counted sales only."""

    location_id: int
    day: date
    sold_item_id: int | None  # the POS sale item, where the line came from one
    product_variation_id: int | None
    units: Decimal
    net_cents: int


def daily_sales(
    db: Session,
    org: OrgContext,
    since: date | None = None,
    until: date | None = None,
    location_id: int | None = None,
) -> list[DailySale]:
    """Counted sales summed per location, business day and thing sold: the latest revision of
    each sale from the source of record, never a source's own records (DR-30)."""
    query = select_scoped(org, SalesTransaction).where(
        SalesTransaction.status == "completed", SalesTransaction.of_record.is_(True)
    )
    if since is not None:
        query = query.where(SalesTransaction.business_date >= since)
    if until is not None:
        query = query.where(SalesTransaction.business_date <= until)
    if location_id is not None:
        query = query.where(SalesTransaction.location_id == location_id)
    transactions = list(db.scalars(query))
    if not transactions:
        return []
    providers = {
        s.id: s.provider
        for s in db.scalars(
            select_scoped(org, SalesSource).where(
                SalesSource.id.in_({t.sales_source_id for t in transactions})
            )
        )
    }
    sold: dict[tuple[str | None, str], int] = {
        (s.provider, s.provider_item_id): s.id for s in db.scalars(select_scoped(org, SoldItem))
    }
    from_sold = {
        v.id: v.source_sold_item_id
        for v in db.scalars(select_scoped(org, ProductVariation))
        if v.source_sold_item_id is not None
    }
    totals: dict[tuple, list] = defaultdict(lambda: [Decimal(0), 0])
    for transaction in transactions:
        provider = providers.get(transaction.sales_source_id)
        for line in transaction.lines:
            sold_item = sold.get((provider, line.external_ref)) if line.external_ref else None
            if sold_item is None and line.product_variation_id is not None:
                sold_item = from_sold.get(line.product_variation_id)
            key = (
                transaction.location_id,
                transaction.business_date,
                sold_item,
                line.product_variation_id,
            )
            totals[key][0] += Decimal(line.quantity)
            totals[key][1] += line.net_cents or 0
    return [
        DailySale(location, day, sold_item, variation, units, net)
        for (location, day, sold_item, variation), (units, net) in totals.items()
    ]


# --- the legacy daily feed (DM-7 bridge until order lines, T3.05) --------------------------------


@dataclass(frozen=True)
class DayOfSales:
    """One POS sale item's sales at a location on a day, as the legacy Square sync and the demo
    produce them."""

    location_id: int
    day: date
    sold_item: SoldItem
    units: Decimal
    gross_cents: int = 0
    net_cents: int = 0


def _pos_source(
    db: Session, org: OrgContext, location_id: int, provider: str, first_day: date
) -> SalesSource:
    existing = [
        s
        for s in sources(db, org, location_id)
        if s.kind == "pos_adapter" and s.provider == provider
    ]
    if existing:
        source = existing[0]
        if source.of_record and first_day < source.active_from:
            earlier = Span(first_day, source.active_from - timedelta(days=1))
            others = [s for s in sources(db, org, location_id) if s.of_record and s.id != source.id]
            if not any(_span(s).overlaps(earlier) for s in others):
                # Older days arriving later: the feed has been the source of record all along.
                source.active_from = first_day
                db.flush()
        return source
    taken = any(
        s.of_record and _span(s).overlaps(Span(first_day)) for s in sources(db, org, location_id)
    )
    return add_source(
        db,
        org,
        location_id=location_id,
        kind="pos_adapter",
        provider=provider,
        active_from=first_day,
        of_record=not taken,
    )


def record_daily_pos_sales(
    db: Session, org: OrgContext, provider: str, days: Iterable[DayOfSales]
) -> int:
    """A POS's sales summed by day, recorded as one daily transaction per location and day
    (reference "daily:<date>"), a line per sale item: what the legacy Square sync and the demo
    write now that `sales_daily` is frozen (DM-7). Returns how many days were new or changed."""
    by_day: dict[tuple[int, date], list[DayOfSales]] = defaultdict(list)
    for entry in days:
        by_day[(entry.location_id, entry.day)].append(entry)
    if not by_day:
        return 0
    variation_of = {
        v.source_sold_item_id: v.id
        for v in db.scalars(select_scoped(org, ProductVariation))
        if v.source_sold_item_id is not None
    }
    first: dict[int, date] = {}
    for location_id, day in by_day:
        first[location_id] = min(day, first.get(location_id, day))
    written = 0
    for (location_id, day), entries in sorted(by_day.items(), key=lambda kv: kv[0][1]):
        source = _pos_source(db, org, location_id, provider, first[location_id])
        lines = [
            SaleLine(
                f"item:{e.sold_item.provider_item_id}",
                e.units,
                variation_of.get(e.sold_item.id),
                e.sold_item.provider_item_id,
                e.sold_item.name,
                (),
                e.gross_cents,
                e.net_cents,
            )
            for e in entries
        ]
        made = record_sale(
            db,
            org,
            source,
            source_reference=f"daily:{day.isoformat()}",
            lines=lines,
            day=day,
            gross_cents=sum(e.gross_cents for e in entries),
            net_cents=sum(e.net_cents for e in entries),
        )
        written += made is not None
    log.info("recorded %d days of %s sales for tenant %s", written, provider, org.tenant_id)
    return written
