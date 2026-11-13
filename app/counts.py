"""Count sessions (T1.23; REC-FR-01..03, -05..08, DR-12, PD-12, WF-09, AC-REC-01..04).

The only writer of stock_counts and stock_count_lines. A session covers one location: all of it,
a category, a storage area or one item, listed in storage order. Each line is an observation in
any unit or package, parts added ("3 cases + 4 each"), stamped with when and by whom it was
counted. Posting happens once, conditionally (DR-07): each line's expected is what the ledger held
at its own instant, each non-zero variance becomes a `count_variance`, and lines worth reviewing
open `count_variance_high` for a Manager after posting (R1, PD-12). Nothing here sets a balance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped, update_scoped
from app.exceptions import record_exception
from app.items import entered_base
from app.ledger.posting import Observation, balance_at, post_count_lines
from app.models import (
    InventoryCategory,
    InventoryItem,
    ItemLocation,
    Location,
    StockCount,
    StockCountLine,
    StockItem,
)
from app.transform.counts import (
    CountRuleError,
    check_parts,
    check_reason,
    check_scope,
    needs_review,
    variance_cents,
)
from app.transform.exceptions import Finding
from app.units import format_change, format_qty

log = logging.getLogger(__name__)


class NotFound(LookupError):
    """No such session, location or item that org may see."""


@dataclass(frozen=True)
class CountItem:
    """One item to count in a session, in storage order."""

    item: InventoryItem
    setting: ItemLocation
    stock: StockItem


def _session(db: Session, org: OrgContext, count_id: int) -> StockCount:
    # Read afresh: sessions keep loaded rows across commits, and whether a count is still open
    # must be what the database says now, not what this session saw earlier.
    count = db.scalars(
        select_scoped(org, StockCount)
        .where(StockCount.id == count_id)
        .execution_options(populate_existing=True)
    ).first()
    if count is None:
        raise NotFound("count not found")
    return count


def start_count(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    scope: str,
    scope_ref: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> StockCount:
    """Open a session (REC-FR-01). Its items are listed by count_items()."""
    location = get_scoped(db, org, Location, location_id)
    if location is None:
        raise NotFound("location not found")
    ref = check_scope(scope, scope_ref)
    if scope == "category" and (
        not (ref or "").isdigit() or get_scoped(db, org, InventoryCategory, int(ref or 0)) is None
    ):
        raise NotFound("category not found")
    if scope == "item" and not _stocked(db, org, location.id, ref):
        raise NotFound("item not stocked there")
    count = StockCount(
        tenant_id=org.tenant_id,
        location_id=location.id,
        scope=scope,
        scope_ref=ref,
        note=" ".join((note or "").split())[:300] or None,
        started_by_user_id=org.user_id,
        started_at=now or datetime.now(UTC),
    )
    db.add(count)
    db.flush()
    return count


def _stocked(db: Session, org: OrgContext, location_id: int, ref: str | None) -> bool:
    if not (ref or "").isdigit():
        return False
    return (
        db.scalars(
            select_scoped(org, ItemLocation).where(
                ItemLocation.location_id == location_id,
                ItemLocation.inventory_item_id == int(ref or 0),
                ItemLocation.is_active.is_(True),
            )
        ).first()
        is not None
    )


def count_items(db: Session, org: OrgContext, count: StockCount) -> list[CountItem]:
    """The session's items in storage order: by storage area, then name (AC-REC-01)."""
    settings = list(
        db.scalars(
            select_scoped(org, ItemLocation).where(
                ItemLocation.location_id == count.location_id, ItemLocation.is_active.is_(True)
            )
        )
    )
    items = {
        item.id: item
        for item in db.scalars(
            select_scoped(org, InventoryItem).where(
                InventoryItem.id.in_([s.inventory_item_id for s in settings]),
                InventoryItem.is_active.is_(True),
            )
        )
    }
    stock = {
        row.inventory_item_id: row
        for row in db.scalars(
            select_scoped(org, StockItem).where(StockItem.location_id == count.location_id)
        )
    }
    found = []
    for setting in settings:
        item = items.get(setting.inventory_item_id)
        row = stock.get(setting.inventory_item_id)
        if item is None or row is None:
            continue
        if count.scope == "category" and str(item.category_id) != count.scope_ref:
            continue
        if count.scope == "area" and (setting.storage_area or "") != count.scope_ref:
            continue
        if count.scope == "item" and str(item.id) != count.scope_ref:
            continue
        found.append(CountItem(item, setting, row))
    return sorted(
        found,
        key=lambda c: (c.setting.storage_area is None, c.setting.storage_area or "", c.item.name),
    )


def record_line(
    db: Session,
    org: OrgContext,
    count_id: int,
    inventory_item_id: int,
    parts: list[tuple[str, str]],
    *,
    reason: str | None = None,
    now: datetime | None = None,
) -> StockCountLine:
    """Record what is on the shelf of one item, in any units or packages (REC-FR-02): each
    (quantity, unit) part is resolved and added. Counting the item again replaces the line."""
    count = _session(db, org, count_id)
    if count.status != "open":
        raise CountRuleError("This count has been posted; start a new one.")
    listed = {c.item.id: c for c in count_items(db, org, count)}
    entry = listed.get(inventory_item_id)
    if entry is None:
        raise NotFound("item not in this count")
    given = [(qty.strip(), unit) for qty, unit in parts if qty.strip()]
    total = check_parts([entered_base(entry.item, qty, unit) for qty, unit in given])
    now = now or datetime.now(UTC)
    line = db.scalars(
        select_scoped(org, StockCountLine).where(
            StockCountLine.stock_count_id == count.id,
            StockCountLine.inventory_item_id == inventory_item_id,
        )
    ).first()
    if line is None:
        line = StockCountLine(
            tenant_id=org.tenant_id,
            stock_count_id=count.id,
            inventory_item_id=inventory_item_id,
        )
        db.add(line)
    line.counted_qty_base = total
    line.entered = " + ".join(f"{qty} {unit}" for qty, unit in given)[:200]
    line.counted_at = now
    line.counted_by_user_id = org.user_id
    line.reason = check_reason(reason)
    # A preview for the counter; posting works it out again for the same instant.
    line.expected_qty_base = balance_at(db, org, count.location_id, inventory_item_id, now)
    db.flush()
    return line


def post_count(
    db: Session, org: OrgContext, count_id: int, now: datetime | None = None
) -> StockCount:
    """Post the session once (DR-07, REC-FR-07): one count_variance per non-zero line, the
    variance in quantity and cost on each line, and count_variance_high for lines to review."""
    count = _session(db, org, count_id)
    if not count.lines:
        raise CountRuleError("Count at least one item before posting.")
    now = now or datetime.now(UTC)
    claim = db.execute(
        update_scoped(org, StockCount)
        .where(StockCount.id == count.id, StockCount.status == "open")
        .values(status="posted", posted_at=now, posted_by_user_id=org.user_id)
        .execution_options(synchronize_session=False)
    )
    if int(getattr(claim, "rowcount", 0) or 0) != 1:
        raise CountRuleError("This count has already been posted.")
    db.refresh(count)
    lines = sorted(count.lines, key=lambda line: line.id)
    transaction, results = post_count_lines(
        db,
        org,
        observations=[
            Observation(
                count.location_id,
                line.inventory_item_id,
                Decimal(line.counted_qty_base),
                line.counted_at,
                reason=f"Count {count.id}",
            )
            for line in lines
        ],
        source_type="count",
        source_id=str(count.id),
        idempotency_key=f"count:{count.id}",
        occurred_at=now,
        reason=f"Count {count.id}",
    )
    count.transaction_id = transaction.id if transaction is not None else None
    location = get_scoped(db, org, Location, count.location_id)
    items = {
        item.id: item
        for item in db.scalars(
            select_scoped(org, InventoryItem).where(
                InventoryItem.id.in_([line.inventory_item_id for line in lines])
            )
        )
    }
    stock = {
        row.inventory_item_id: row
        for row in db.scalars(
            select_scoped(org, StockItem).where(StockItem.location_id == count.location_id)
        )
    }
    for line, result in zip(lines, results, strict=True):
        line.expected_qty_base = result.expected
        line.variance_qty_base = result.variance
        line.unit_cost = result.unit_cost
        line.variance_cents = variance_cents(result.variance, result.unit_cost)
        line.movement_id = result.movement_id
        line.needs_review = needs_review(result.variance, result.expected, line.variance_cents)
        row = stock.get(line.inventory_item_id)
        if row is not None and (
            row.last_counted_at is None or row.last_counted_at < line.counted_at
        ):
            row.last_counted_at = line.counted_at
        if line.needs_review and location is not None:
            item = items[line.inventory_item_id]
            unit = item.display_unit
            record_exception(
                db,
                org,
                Finding(
                    type="count_variance_high",
                    subject_type="count_line",
                    subject_id=str(line.id),
                    title=(
                        f"{item.name} at {location.name}: counted "
                        f"{format_qty(line.counted_qty_base, unit)}, expected "
                        f"{format_qty(result.expected, unit)} "
                        f"({format_change(result.variance, unit)}). Review the count."
                    ),
                    location_id=location.id,
                    money_impact_cents=abs(line.variance_cents),
                    quantity_base=result.variance,
                ),
                now=now,
            )
    db.flush()
    log.info(
        "count %s posted: %d lines, %d variances",
        count.id,
        len(lines),
        sum(1 for r in results if r.movement_id is not None),
    )
    return count


def get_count(db: Session, org: OrgContext, count_id: int) -> StockCount:
    """A session org may see, read afresh; NotFound otherwise."""
    return _session(db, org, count_id)


@dataclass(frozen=True)
class Summary:
    """One session in the reconciliation history (REC-FR-09, AC-REC-05)."""

    count: StockCount
    location_name: str
    lines: int
    variances: int
    to_review: int
    variance_cents: int  # the sum of the lines' variances at cost, signed


def summaries(db: Session, org: OrgContext, location_id: int | None = None) -> list[Summary]:
    """Every session org may see, open ones first, then the most recently posted."""
    query = select_scoped(org, StockCount)
    if location_id is not None:
        query = query.where(StockCount.location_id == location_id)
    places = {place.id: place.name for place in db.scalars(select_scoped(org, Location))}
    found = [
        Summary(
            count=count,
            location_name=places.get(count.location_id, ""),
            lines=len(count.lines),
            variances=sum(1 for line in count.lines if line.movement_id is not None),
            to_review=sum(1 for line in count.lines if line.needs_review),
            variance_cents=sum(line.variance_cents or 0 for line in count.lines),
        )
        for count in db.scalars(query)
    ]
    return sorted(
        found,
        key=lambda s: (
            s.count.status != "open",
            -(s.count.posted_at or s.count.started_at).timestamp(),
        ),
    )
