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

from sqlalchemy import exists
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.audit import record_audit
from app.consumption import queue_consumption, sale_moment
from app.exceptions import record_exception
from app.models import (
    ExternalModifier,
    ExternalProduct,
    Location,
    ProductMapping,
    ProductVariation,
    ProviderConnection,
    SalesLineModifier,
    SalesReturn,
    SalesReturnLine,
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


def extend_source(
    db: Session, org: OrgContext, source_id: int, first_day: date, ip: str | None = None
) -> SalesSource:
    """Start a source earlier, when no other source of record covers the days in between
    (DR-31): a business entering an older day by hand."""
    source = get_scoped(db, org, SalesSource, source_id)
    if source is None:
        raise NotFound(source_id)
    if first_day >= source.active_from:
        return source
    others = [s for s in sources(db, org, source.location_id) if s.of_record and s.id != source.id]
    check_new_source(
        Span(first_day, source.active_from - timedelta(days=1)), (_span(s) for s in others)
    )
    was = source.active_from
    source.active_from = first_day
    db.flush()
    record_audit(
        db,
        org,
        "settings.sales_source_extended",
        f"A sales source now starts on {first_day:%d %b %Y}",
        subject_type="sales_source",
        subject_id=source.id,
        before={"from": str(was)},
        after={"from": str(first_day)},
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


def origins_of_record(
    db: Session, org: OrgContext, location_id: int, first: date, last: date
) -> dict[date, str]:
    """Each business date's source-of-record origin, for the dates that have one (DR-31)."""
    rows = [s for s in sources(db, org, location_id) if s.of_record]
    kinds = {s.id: s.kind for s in rows}
    found = {}
    day = first
    while day <= last:
        source_id = of_record(((s.id, _span(s)) for s in rows), day)
        if source_id is not None:
            found[day] = kinds[source_id]
        day += timedelta(days=1)
    return found


def sold_origins(
    db: Session, org: OrgContext, location_id: int, first: date, last: date
) -> dict[date, set[str]]:
    """The origins of each business date's counted sales (DR-34: a sale keeps its source's). A
    day corrected to nothing has no line, so it is not a day with sales."""
    found: dict[date, set[str]] = defaultdict(set)
    line = exists().where(SalesTransactionLine.sales_transaction_id == SalesTransaction.id)
    for day, origin in db.execute(
        select_scoped(org, SalesTransaction)
        .with_only_columns(SalesTransaction.business_date, SalesTransaction.origin)
        .where(
            SalesTransaction.location_id == location_id,
            SalesTransaction.business_date >= first,
            SalesTransaction.business_date <= last,
            SalesTransaction.status == "completed",
            SalesTransaction.of_record.is_(True),
            line,
        )
        .distinct()
    ):
        found[day].add(origin)
    return dict(found)


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
    consume: bool = True,
    reason: str | None = None,
) -> SalesTransaction | None:
    """Record a sale (or a day's sales) from a source; None when a resend changes nothing.

    Its business date is its location's for `occurred_at` (ADR-0027), or the day given for a
    daily entry. A sale from a source that is not the source of record that day is kept,
    flagged `sales_source_overlap` and never counted (DR-31).

    What it used leaves stock in the background (T2.05, `consume_sale`), and a correction
    gives back what the revision before it took. `consume=False` keeps history for reference
    only (the demo's past days): it is recorded and never consumed."""
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
            consumption_status="pending" if counted and consume else "skipped",
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
    if consume:
        queue_consumption(db, org, transaction, reason)
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
    external_ref: str | None = None  # the POS's id, for a line that is neither of the above


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
            # A POS line that is neither one of our sizes nor a legacy sale item keeps the POS's
            # own id, so each unmapped product stays apart (T3.12: no new sale items).
            ref = (
                line.external_ref
                if sold_item is None and line.product_variation_id is None
                else None
            )
            key = (
                transaction.location_id,
                transaction.business_date,
                sold_item,
                line.product_variation_id,
                ref,
            )
            totals[key][0] += Decimal(line.quantity)
            totals[key][1] += line.net_cents or 0
    return [
        DailySale(location, day, sold_item, variation, units, net, ref)
        for (location, day, sold_item, variation, ref), (units, net) in totals.items()
    ]


# --- the legacy daily feed (DM-7 bridge until order lines, T3.05) --------------------------------


@dataclass(frozen=True)
class DayOfSales:
    """One POS sale item's sales at a location on a day, as the demo produces them (the legacy
    Square sync that also did is gone, T3.12; its days stay as history)."""

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
        # The one counting that day, else the one of record, else the latest.
        source = next(
            (s for s in existing if s.of_record and _span(s).overlaps(Span(first_day))),
            next((s for s in reversed(existing) if s.of_record), existing[-1]),
        )
        if source.of_record and not source.fixed_from and first_day < source.active_from:
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
    db: Session,
    org: OrgContext,
    provider: str,
    days: Iterable[DayOfSales],
    *,
    consume: bool = True,
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
    # A mapping wins over the size a sold item was made into (T3.04): it is the business's word.
    mapped = mapped_variations(db, org, provider)
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
                mapped.get(e.sold_item.provider_item_id, variation_of.get(e.sold_item.id)),
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
            consume=consume,
        )
        written += made is not None
    log.info("recorded %d days of %s sales for tenant %s", written, provider, org.tenant_id)
    return written


# --- POS lines and their mappings (T3.04, WF-28) --------------------------------------------------


def mapped_variations(db: Session, org: OrgContext, provider: str) -> dict[str, int]:
    """Each POS variation id the business has mapped, to its product size (the latest
    connection's mapping when two connections name the same id)."""
    rows = db.execute(
        select_scoped(org, ProductMapping)
        .with_only_columns(
            ExternalProduct.external_variation_id, ProductMapping.product_variation_id
        )
        .join(ExternalProduct, ExternalProduct.id == ProductMapping.external_product_id)
        .join(ProviderConnection, ProviderConnection.id == ExternalProduct.connection_id)
        .where(ProductMapping.status == "active", ProviderConnection.provider == provider)
        .order_by(ProviderConnection.id)
    ).tuples()
    return {external: variation for external, variation in rows if variation is not None}


def _pos_lines(
    db: Session,
    org: OrgContext,
    provider: str,
    external_ref: str | None = None,
    since: date | None = None,
) -> list[tuple[SalesTransaction, SalesTransactionLine]]:
    """Counted lines of the provider's sales that name a POS item."""
    query = (
        select_scoped(org, SalesTransactionLine)
        .add_columns(SalesTransaction)
        .join(SalesTransaction, SalesTransaction.id == SalesTransactionLine.sales_transaction_id)
        .join(SalesSource, SalesSource.id == SalesTransaction.sales_source_id)
        .where(
            SalesSource.provider == provider,
            SalesTransaction.status == "completed",
            SalesTransaction.of_record.is_(True),
            SalesTransactionLine.external_ref.is_not(None),
        )
    )
    if external_ref is not None:
        query = query.where(SalesTransactionLine.external_ref == external_ref)
    if since is not None:
        query = query.where(SalesTransaction.business_date >= since)
    return [(transaction, line) for line, transaction in db.execute(query).tuples()]


def unmapped_units(db: Session, org: OrgContext, provider: str) -> dict[str, Decimal]:
    """How many of each POS item were sold with no product size (AC-RCP-08's counterpart for
    the POS): its sales used nothing from stock."""
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for _transaction, line in _pos_lines(db, org, provider):
        if line.product_variation_id is None and line.external_ref:
            totals[line.external_ref] += Decimal(line.quantity)
    return dict(totals)


def lines_to_remap(
    db: Session, org: OrgContext, provider: str, external_ref: str, variation_id: int, since: date
) -> list[tuple[SalesTransaction, SalesTransactionLine]]:
    """The counted lines since a business date that do not yet name this size for the POS item:
    what reprocessing a mapping changes."""
    return [
        (transaction, line)
        for transaction, line in _pos_lines(db, org, provider, external_ref, since)
        if line.product_variation_id != variation_id
    ]


def lines_of_size(
    db: Session, org: OrgContext, variation_id: int, since: date
) -> list[tuple[SalesTransaction, SalesTransactionLine]]:
    """The counted lines of a product size since a business date that have taken stock (or
    found a gap): what applying a back-dated recipe to past sales may change (C-51)."""
    return [
        (transaction, line)
        for transaction in db.scalars(
            select_scoped(org, SalesTransaction).where(
                SalesTransaction.status == "completed",
                SalesTransaction.of_record.is_(True),
                SalesTransaction.business_date >= since,
            )
        )
        for line in transaction.lines
        if line.product_variation_id == variation_id
        and line.consumption_status in ("posted", "gap")
    ]


def remap_lines(
    db: Session, org: OrgContext, provider: str, external_ref: str, variation_id: int, since: date
) -> list[int]:
    """Point those lines at the mapped size, so the next resolution uses it; the sales whose
    lines changed. Nothing about the sale itself changes."""
    changed: set[int] = set()
    for transaction, line in lines_to_remap(db, org, provider, external_ref, variation_id, since):
        line.product_variation_id = variation_id
        changed.add(transaction.id)
    db.flush()
    return sorted(changed)


# --- POS orders (T3.05) ---------------------------------------------------------------------------


def record_pos_order(
    db: Session,
    org: OrgContext,
    provider: str,
    location_id: int,
    *,
    reference: str,
    occurred_at: datetime,
    lines: Sequence[SaleLine],
    gross_cents: int | None = None,
    net_cents: int | None = None,
) -> SalesTransaction | None:
    """A completed POS order as one sale at its location and moment, through the location's POS
    source (created on first use); None when a resend changes nothing."""
    location = get_scoped(db, org, Location, location_id)
    if location is None:
        raise NotFound(location_id)
    day = business_date(occurred_at, location.timezone, location.business_day_cutoff_hour)
    source = _pos_source(db, org, location_id, provider, day)
    sale = record_sale(
        db,
        org,
        source,
        source_reference=reference,
        lines=lines,
        occurred_at=occurred_at,
        gross_cents=gross_cents,
        net_cents=net_cents,
    )
    if sale is not None:
        link_waiting_refunds(db, org, sale)
    return sale


def void_sale(db: Session, org: OrgContext, transaction: SalesTransaction, reason: str) -> bool:
    """A completed sale that no longer stands: it stops counting and what it took from stock is
    given back (WF-12); False when it was not completed."""
    if transaction.status != "completed":
        return False
    transaction.status = "voided"
    db.flush()
    queue_consumption(db, org, transaction, reason)
    return True


def retire_daily_totals(
    db: Session, org: OrgContext, provider: str, since: date, reason: str
) -> int:
    """Void the POS's legacy daily totals from a business date on, where its order lines take
    over (T3.05): the same orders, counted once. How many days were voided."""
    totals = list(
        db.scalars(
            select_scoped(org, SalesTransaction)
            .join(SalesSource, SalesSource.id == SalesTransaction.sales_source_id)
            .where(
                SalesSource.provider == provider,
                SalesSource.kind == "pos_adapter",
                SalesTransaction.status == "completed",
                SalesTransaction.source_reference.startswith("daily:"),
                SalesTransaction.business_date >= since,
            )
        )
    )
    return sum(void_sale(db, org, t, reason) for t in totals)


def mapped_modifiers(db: Session, org: OrgContext, provider: str) -> dict[str, int]:
    """Each POS modifier id the business has mapped, to its modifier."""
    rows = db.execute(
        select_scoped(org, ProductMapping)
        .with_only_columns(ExternalModifier.external_modifier_id, ProductMapping.modifier_id)
        .join(ExternalModifier, ExternalModifier.id == ProductMapping.external_modifier_id)
        .join(ProviderConnection, ProviderConnection.id == ExternalModifier.connection_id)
        .where(ProductMapping.status == "active", ProviderConnection.provider == provider)
        .order_by(ProviderConnection.id)
    ).tuples()
    return {external: modifier for external, modifier in rows if modifier is not None}


# --- refunds and sales money (T3.07, WF-17, DR-26) ------------------------------------------------


def _link_return(
    db: Session, org: OrgContext, row: SalesReturn, original: SalesTransaction
) -> None:
    """Tie a return to its sale and each returned line, by its key, to the line given back; a
    returned line's amount is its share of that line's net."""
    row.sales_transaction_id = original.id
    by_key = {ln.line_key: ln for ln in original.lines}
    for back in row.lines:
        sold = by_key.get(back.line_key or "")
        if sold is None:
            continue
        back.sales_transaction_line_id = sold.id
        if sold.net_cents is not None and Decimal(sold.quantity) > 0:
            back.amount_cents = int(
                (
                    Decimal(sold.net_cents) * Decimal(back.quantity) / Decimal(sold.quantity)
                ).quantize(Decimal(1))
            )
    db.flush()


def record_pos_refund(
    db: Session,
    org: OrgContext,
    provider: str,
    location_id: int,
    *,
    reference: str,
    original_reference: str,
    occurred_at: datetime,
    amount_cents: int | None,
    lines: Sequence[tuple[str, Decimal]] = (),
) -> SalesReturn | None:
    """A POS refund as a sales return linked to its sale (POS-FR-08): it lowers net sales and
    never puts stock back (DR-26). Recorded once per refund id; None when it was before. A refund
    whose order has not arrived keeps its lines by key and is linked when the order does."""
    location = get_scoped(db, org, Location, location_id)
    if location is None:
        raise NotFound(location_id)
    day = business_date(occurred_at, location.timezone, location.business_day_cutoff_hour)
    source = _pos_source(db, org, location_id, provider, day)
    taken = db.scalars(
        select_scoped(org, SalesReturn).where(
            SalesReturn.sales_source_id == source.id, SalesReturn.source_reference == reference
        )
    ).first()
    if taken is not None:
        return None
    row = SalesReturn(
        tenant_id=org.tenant_id,
        location_id=location_id,
        sales_source_id=source.id,
        origin=source.kind,
        source_reference=reference[:200],
        original_reference=original_reference[:200],
        business_date=day,
        amount_cents=amount_cents,
    )
    db.add(row)
    db.flush()
    for line_key, quantity in lines:
        if quantity > 0:
            db.add(
                SalesReturnLine(
                    tenant_id=org.tenant_id,
                    sales_return_id=row.id,
                    line_key=line_key[:120],
                    quantity=quantity,
                )
            )
    db.flush()
    db.refresh(row)
    original = _latest(db, org, source, original_reference)
    if original is not None:
        _link_return(db, org, row, original)
    return row


def link_waiting_refunds(db: Session, org: OrgContext, original: SalesTransaction) -> int:
    """Refunds that arrived before their order, linked to it now (WF-17); how many."""
    waiting = list(
        db.scalars(
            select_scoped(org, SalesReturn).where(
                SalesReturn.sales_source_id == original.sales_source_id,
                SalesReturn.original_reference == original.source_reference,
                SalesReturn.sales_transaction_id.is_(None),
            )
        )
    )
    for row in waiting:
        _link_return(db, org, row, original)
    return len(waiting)


@dataclass(frozen=True)
class SalesMoney:
    """Counted sales over business dates, and what was given back (AC-POS-05)."""

    gross_cents: int
    refunded_cents: int

    @property
    def net_cents(self) -> int:
        return self.gross_cents - self.refunded_cents


def sales_money(
    db: Session, org: OrgContext, since: date, until: date, location_id: int | None = None
) -> SalesMoney:
    """Net sales of the source of record, less the refunds of the source of record on the days
    they were given (DR-26: a refund changes money, never stock)."""
    query = select_scoped(org, SalesTransaction).where(
        SalesTransaction.status == "completed",
        SalesTransaction.of_record.is_(True),
        SalesTransaction.business_date >= since,
        SalesTransaction.business_date <= until,
    )
    returns = select_scoped(org, SalesReturn).where(
        SalesReturn.business_date >= since, SalesReturn.business_date <= until
    )
    if location_id is not None:
        query = query.where(SalesTransaction.location_id == location_id)
        returns = returns.where(SalesReturn.location_id == location_id)
    gross = sum(t.net_cents or 0 for t in db.scalars(query))
    refunded = 0
    for row in db.scalars(returns):
        record = source_of_record(db, org, row.location_id, row.business_date)
        if record is not None and record.id == row.sales_source_id:
            refunded += row.amount_cents or 0
    return SalesMoney(gross, refunded)


# --- cutover between sources (T3.10, WF-25, DR-31) -------------------------------------------------


@dataclass(frozen=True)
class Cutover:
    """What moving a location's sales to another source from a business date does."""

    voided: tuple[tuple[date, str], ...]  # counted sales of the old source from then: (day, origin)
    counted: tuple[date, ...]  # sales the new source already recorded from then, now counted
    first_moment: datetime | None = None  # the earliest sale either changes (C-53)


def _target(
    db: Session, org: OrgContext, location_id: int, kind: str, provider: str | None
) -> SalesSource | None:
    found = [
        s
        for s in sources(db, org, location_id)
        if s.kind == kind and (provider is None or s.provider == provider)
    ]
    return max(found, key=lambda s: (s.of_record, s.active_from), default=None)


def _counted_from(
    db: Session, org: OrgContext, source_ids: Iterable[int], since: date, of_record: bool
) -> list[SalesTransaction]:
    ids = list(source_ids)
    if not ids:
        return []
    return list(
        db.scalars(
            select_scoped(org, SalesTransaction).where(
                SalesTransaction.sales_source_id.in_(ids),
                SalesTransaction.status == "completed",
                SalesTransaction.of_record.is_(of_record),
                SalesTransaction.business_date >= since,
            )
        )
    )


def cutover_plan(
    db: Session,
    org: OrgContext,
    location_id: int,
    kind: str,
    provider: str | None,
    cutover: date,
) -> Cutover:
    """The preview of `cut_over()`: nothing is written."""
    target = _target(db, org, location_id, kind, provider)
    others = [s for s in sources(db, org, location_id) if s.of_record and s is not target]
    voided = _counted_from(db, org, (s.id for s in others), cutover, True)
    counted = _counted_from(db, org, [target.id] if target else [], cutover, False)
    moments = [sale_moment(db, org, t) for t in [*voided, *counted]]
    return Cutover(
        tuple(sorted((t.business_date, t.origin) for t in voided)),
        tuple(sorted({t.business_date for t in counted})),
        min(moments, default=None),
    )


def cut_over(
    db: Session,
    org: OrgContext,
    location_id: int,
    kind: str,
    provider: str | None,
    cutover: date,
    reason: str,
) -> Cutover:
    """From `cutover` on, the location's sales come from `kind` (and `provider`) and from it
    alone (DR-31): every other source of record stops the day before, its counted sales from then
    are voided (their consumption given back), and what the new source already recorded from then
    is counted and consumed. Earlier days are untouched."""
    plan = cutover_plan(db, org, location_id, kind, provider, cutover)
    target = _target(db, org, location_id, kind, provider)
    for source in sources(db, org, location_id):
        if source is target or not source.of_record:
            continue
        if source.active_from >= cutover:
            source.of_record = False  # it never began before the cutover: retired whole
        elif source.active_to is None or source.active_to >= cutover:
            source.active_to = cutover - timedelta(days=1)
    db.flush()
    for sale in _counted_from(
        db, org, (s.id for s in sources(db, org, location_id) if s is not target), cutover, True
    ):
        void_sale(db, org, sale, reason)
    if target is None:
        target = add_source(
            db, org, location_id=location_id, kind=kind, provider=provider, active_from=cutover
        )
    elif not target.of_record or target.active_from > cutover:
        target.active_from = cutover
    target.of_record, target.active_to, target.fixed_from = True, None, True
    db.flush()
    for sale in _counted_from(db, org, [target.id], cutover, False):
        sale.of_record = True
        for line in sale.lines:
            if line.consumption_status == "skipped":
                line.consumption_status = "pending"
        queue_consumption(db, org, sale, reason)
    db.flush()
    return plan
