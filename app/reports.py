"""Operational reports (T2.16; ANL-FR-03, -05, -07, AC-ANL-03, -05).

Reads only: ledger movements over each location's own business days (its time zone and cutoff
hour, AC-ANL-05), waste events for their reason, and batches for stock past its date. The
figures are worked out in `app/transform/analytics.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.costing import costing_location, size_costs
from app.counts import counted_lines
from app.ledger.history import ItemMove, balances_before, item_moves
from app.ledger.posting import batch_remaining
from app.models import (
    Location,
    Product,
    Receiving,
    SalesTransaction,
    StockBatch,
    StockItem,
    Supplier,
    WasteEvent,
    WasteReason,
)
from app.receivings import discrepancies
from app.sales import origins_of_record, sold_origins
from app.transform.analytics import (
    AtRisk,
    ConsumptionReport,
    CostOfSales,
    Count,
    ExpiryReport,
    Interval,
    Moved,
    Price,
    PriceChange,
    SalesCoverage,
    Timed,
    VarianceReport,
    consumption,
    count_intervals,
    count_variance,
    expiry_losses,
    price_changes,
    sales_coverage,
)
from app.transform.business_days import business_date, business_day_end, business_day_start
from app.transform.dashboard import DateRange
from app.transform.inventory import valuation_cents
from app.transform.receivings import Discrepancies
from app.transform.reorder import Move, Observation, observations

EXPIRED_REASON = "expiration"  # the waste reason code for stock past its date (WST-FR-02)
WASTE_SOURCE = "waste_event"


def _places(db: Session, org: OrgContext, location_id: int | None) -> list[Location]:
    query = select_scoped(org, Location)
    if location_id is not None:
        query = query.where(Location.id == location_id)
    return list(db.scalars(query.order_by(Location.id)))


def days(
    db: Session, org: OrgContext, period: DateRange, now: datetime, location_id: int | None
) -> dict[int, tuple[date, date]]:
    """The period's first and last business day at each location, against its own today."""
    return {
        p.id: period.days_at(business_date(now, p.timezone, p.business_day_cutoff_hour))
        for p in _places(db, org, location_id)
    }


def _windows(
    db: Session, org: OrgContext, period: DateRange, now: datetime, location_id: int | None
) -> dict[int, tuple[datetime, datetime]]:
    spans = days(db, org, period, now, location_id)
    return {
        p.id: (
            business_day_start(spans[p.id][0], p.timezone, p.business_day_cutoff_hour),
            business_day_end(spans[p.id][1], p.timezone, p.business_day_cutoff_hour),
        )
        for p in _places(db, org, location_id)
    }


def _expired_events(db: Session, org: OrgContext, moves: list[ItemMove]) -> set[str]:
    ids = {int(m.source_id) for m in moves if m.source_type == WASTE_SOURCE and m.source_id}
    if not ids:
        return set()
    expired = {
        r.id
        for r in db.scalars(
            select_scoped(org, WasteReason).where(WasteReason.code == EXPIRED_REASON)
        )
    }
    return {
        str(e.id)
        for e in db.scalars(select_scoped(org, WasteEvent).where(WasteEvent.id.in_(ids)))
        if e.reason_id in expired
    }


def _moved(
    db: Session, org: OrgContext, period: DateRange, now: datetime, location_id: int | None
) -> list[Moved]:
    moves = item_moves(db, org, _windows(db, org, period, now, location_id))
    expired = _expired_events(db, org, moves)
    return [
        Moved(
            m.location_id,
            m.inventory_item_id,
            m.movement_type,
            m.qty_base,
            m.unit_cost,
            m.source_type == WASTE_SOURCE and m.source_id in expired,
        )
        for m in moves
    ]


def consumption_report(
    db: Session,
    org: OrgContext,
    period: DateRange,
    *,
    location_id: int | None = None,
    now: datetime | None = None,
) -> ConsumptionReport:
    """Ingredient use by item and period: sales, production, waste, manual (ANL-FR-03)."""
    return consumption(_moved(db, org, period, now or datetime.now(UTC), location_id))


def variance_report(
    db: Session,
    org: OrgContext,
    period: DateRange,
    *,
    location_id: int | None = None,
    now: datetime | None = None,
) -> VarianceReport:
    """Count variances by item and period, quantity and value (ANL-FR-07)."""
    return count_variance(_moved(db, org, period, now or datetime.now(UTC), location_id))


def expiry_report(
    db: Session,
    org: OrgContext,
    period: DateRange,
    *,
    location_id: int | None = None,
    now: datetime | None = None,
) -> ExpiryReport:
    """Waste of stock past its date over the period, and expired stock still held (ANL-FR-05):
    a batch dated before its location's today that holds stock, at its own cost, else the
    item's current cost."""
    now = now or datetime.now(UTC)
    places = {p.id: p for p in _places(db, org, location_id)}
    today = {
        pid: business_date(now, p.timezone, p.business_day_cutoff_hour) for pid, p in places.items()
    }
    stock = {
        s.id: s
        for s in db.scalars(select_scoped(org, StockItem))
        if s.location_id in places and s.inventory_item_id is not None
    }
    batches = [
        b
        for b in db.scalars(select_scoped(org, StockBatch))
        if b.stock_item_id in stock and b.expires_on < today[stock[b.stock_item_id].location_id]
    ]
    held = batch_remaining(db, org, [b.id for b in batches])
    risky = []
    for batch in batches:
        remaining = held.get(batch.id)
        if remaining is None or remaining <= 0:
            continue
        row = stock[batch.stock_item_id]
        cost = batch.cost_per_base_cents or row.cost_per_base_cents
        risky.append(
            AtRisk(
                row.location_id,
                row.inventory_item_id or 0,
                batch.id,
                batch.expires_on,
                remaining,
                valuation_cents(remaining, cost) if cost else None,
            )
        )
    return expiry_losses(_moved(db, org, period, now, location_id), risky)


# --- Reports II (T2.17) -------------------------------------------------------------------------


def cost_of_sales_report(
    db: Session,
    org: OrgContext,
    period: DateRange,
    *,
    location_id: int | None = None,
    now: datetime | None = None,
) -> CostOfSales:
    """Realized ingredient cost of sales (ANL-FR-12): what sale consumption took at each
    movement's snapshot, with the share of counted sale lines fully consumed through a recipe
    (AC-ANL-04). Lines kept as history and never meant to take stock are left out."""
    now = now or datetime.now(UTC)
    used = consumption_report(db, org, period, location_id=location_id, now=now)
    statuses: list[str] = []
    for location, (first, last) in days(db, org, period, now, location_id).items():
        for sale in db.scalars(
            select_scoped(org, SalesTransaction).where(
                SalesTransaction.location_id == location,
                SalesTransaction.business_date >= first,
                SalesTransaction.business_date <= last,
                SalesTransaction.status == "completed",
                SalesTransaction.of_record.is_(True),
            )
        ):
            statuses.extend(
                line.consumption_status
                for line in sale.lines
                if line.consumption_status != "skipped" and Decimal(line.quantity) > 0
            )
    return CostOfSales(
        used.totals_cents["sales"],
        len(statuses),
        sum(1 for status in statuses if status == "posted"),
        any(row.uncosted and row.qty["sales"] > 0 for row in used.rows.values()),
    )


def sales_coverage_report(
    db: Session,
    org: OrgContext,
    period: DateRange,
    *,
    location_id: int | None = None,
    now: datetime | None = None,
) -> list[SalesCoverage]:
    """Where each location's counted sales over the period came from (manual, imported, POS),
    and on its Standalone days, how many open days have sales (STD-FR-07, AC-SAL-08)."""
    now = now or datetime.now(UTC)
    spans = days(db, org, period, now, location_id)
    found = []
    for place in _places(db, org, location_id):
        first, last = spans[place.id]
        today = business_date(now, place.timezone, place.business_day_cutoff_hour)
        until = min(last, today)
        found.append(
            sales_coverage(
                place.id,
                first,
                last,
                today,
                place.open_days,
                origins_of_record(db, org, place.id, first, until) if first <= until else {},
                sold_origins(db, org, place.id, first, until) if first <= until else {},
            )
        )
    return found


def count_usage_report(
    db: Session,
    org: OrgContext,
    period: DateRange,
    *,
    location_id: int | None = None,
    now: datetime | None = None,
) -> dict[tuple[int, int], list[Interval]]:
    """Actual usage per item per interval between consecutive counts in the period (ANL-FR-14,
    STD-FR-08): it needs no sales data. Counts are count-session lines and quick counts."""
    now = now or datetime.now(UTC)
    places = {p.id: p for p in _places(db, org, location_id)}
    windows = _windows(db, org, period, now, location_id)
    opening = balances_before(db, org, windows)
    by_item: dict[tuple[int, int], list[ItemMove]] = {}
    for m in item_moves(db, org, windows):
        by_item.setdefault((m.location_id, m.inventory_item_id), []).append(m)
    sessions = counted_lines(db, org, windows)
    result = {}
    for key in set(by_item) | set(sessions):
        place = places[key[0]]
        zone, cutoff = place.timezone, place.business_day_cutoff_hour
        moves = [
            Move(
                m.occurred_at,
                business_date(m.occurred_at, zone, cutoff),
                m.transaction_id,
                m.kind,
                m.source_type,
                m.movement_type,
                m.qty_base,
            )
            for m in by_item.get(key, [])
        ]
        seen = observations(
            opening.get(key, Decimal(0)),
            moves,
            [
                Observation(at, business_date(at, zone, cutoff), qty)
                for at, qty in sessions.get(key, [])
            ],
        )
        intervals = count_intervals(
            [Timed(m.at, m.movement_type, m.qty_base) for m in moves],
            [Count(o.at, o.day, o.qty_base) for o in seen],
        )
        if intervals:
            result[key] = intervals
    return result


@dataclass(frozen=True)
class Shortfall:
    """A posted receiving line whose quantities did not match (ANL-FR-08)."""

    location_id: int
    receiving_id: int
    supplier: str
    item_id: int
    unit: str
    found: Discrepancies
    posted_at: datetime


@dataclass(frozen=True)
class ReceivingReport:
    shortfalls: tuple[Shortfall, ...]
    changes: tuple[PriceChange, ...]
    suppliers: dict[int, str]


def receiving_report(
    db: Session,
    org: OrgContext,
    period: DateRange,
    *,
    location_id: int | None = None,
    now: datetime | None = None,
) -> ReceivingReport:
    """Receiving discrepancies posted in the period, and supplier price changes paid in it
    (ANL-FR-08): (new - previous) / previous per base unit, from posted receiving lines."""
    now = now or datetime.now(UTC)
    windows = _windows(db, org, period, now, location_id)
    suppliers = {s.id: s.name for s in db.scalars(select_scoped(org, Supplier))}
    shortfalls = []
    prices = []
    for receiving in db.scalars(
        select_scoped(org, Receiving).where(
            Receiving.status == "posted", Receiving.location_id.in_(list(windows))
        )
    ):
        posted = receiving.posted_at
        if posted is None:
            continue
        start, end = windows[receiving.location_id]
        for line in receiving.lines:
            if line.status == "open":
                continue
            if (
                receiving.supplier_id is not None
                and line.unit_price_cents is not None
                and Decimal(line.qty_base) > 0
                and Decimal(line.accepted_qty) > 0
                and posted <= end
            ):
                per_unit = Decimal(line.qty_base) / Decimal(line.accepted_qty)
                prices.append(
                    Price(
                        (receiving.supplier_id, line.inventory_item_id),
                        posted,
                        (Decimal(line.unit_price_cents) / per_unit).quantize(Decimal("0.0001")),
                        receiving.id,
                    )
                )
            found = discrepancies(line)
            if found.any and start <= posted <= end:
                shortfalls.append(
                    Shortfall(
                        receiving.location_id,
                        receiving.id,
                        suppliers.get(receiving.supplier_id or 0, ""),
                        line.inventory_item_id,
                        line.unit,
                        found,
                        posted,
                    )
                )
    since = min((start for start, _ in windows.values()), default=now)
    return ReceivingReport(
        tuple(sorted(shortfalls, key=lambda s: s.posted_at)),
        tuple(price_changes(prices, since)),
        suppliers,
    )


@dataclass(frozen=True)
class MarginRow:
    product: str
    size: str
    variation_id: int
    price_cents: int | None
    cost_cents: Decimal | None  # per serving; None without a recipe in effect
    complete: bool
    percent: Decimal | None


def margin_report(
    db: Session, org: OrgContext, *, location_id: int | None = None
) -> tuple[Location | None, list[MarginRow]]:
    """Recipe cost and estimated gross margin by product size at one location, at today's
    costs (ANL-FR-06)."""
    place = costing_location(db, org, location_id)
    if place is None:
        return None, []
    rows = []
    for product in db.scalars(select_scoped(org, Product).where(Product.is_active.is_(True))):
        for sized in size_costs(db, org, product, place.id):
            if not sized.variation.is_active:
                continue
            rows.append(
                MarginRow(
                    product.name,
                    sized.variation.name,
                    sized.variation.id,
                    sized.variation.price_cents,
                    sized.cost.per_yield_cents if sized.cost else None,
                    bool(sized.margin and sized.margin.complete),
                    sized.margin.percent if sized.margin else None,
                )
            )
    rows.sort(key=lambda r: (r.product.lower(), r.size.lower()))
    return place, rows
