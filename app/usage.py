"""Direct usage records (T2.14; STD-FR-04, WF-16).

Ingredients used without a sale (staff meals, samples, catering by weight) leave stock as one
`usage` transaction of `manual_consumption` movements keyed `usage:<token>`; the token comes
from the form, so the same form sent twice posts once. The transaction is the record: a reason
is required, and below zero is refused without the override (DR-08). What was used over a
period is read from the ledger (DR-33), with use by sales and manual usage kept apart.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, members, select_scoped
from app.adjustments import negative_finding
from app.consumption import SOURCE as SALE_SOURCE
from app.costs import cost_on
from app.exceptions import record_exception
from app.items import entered_base
from app.ledger.history import movements_in
from app.ledger.posting import balance, post_transaction, posted_count
from app.models import InventoryItem, ItemLocation, Location
from app.transform.business_days import business_day_end, business_day_start
from app.transform.exceptions import Finding
from app.transform.items import ItemRuleError
from app.transform.ledger import Line, places
from app.transform.usage import MAX_LINES, SOURCE, UsageRuleError, check_reason, check_token

log = logging.getLogger(__name__)

OVERRIDE = "adjustment.override_negative"


class NotFound(LookupError):
    """No such location or item that org may see."""


@dataclass(frozen=True)
class Use:
    """One ingredient as someone entered it: a quantity in a unit or package of the item."""

    inventory_item_id: int
    quantity: str
    unit: str = ""


def new_token() -> str:
    """A fresh token for a usage form."""
    return uuid.uuid4().hex


def record_usage(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    uses: list[Use],
    reason: str,
    token: str,
    note: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Post a usage record (STD-FR-04); False when this form was already posted. Refused with
    UsageRuleError, or with BelowZero when it would take stock below zero and the person may
    not (DR-08); the caller rolls back."""
    place = get_scoped(db, org, Location, location_id)
    if place is None:
        raise NotFound(location_id)
    why = check_reason(reason, note)
    key = check_token(token)
    entries = [use for use in uses if use.quantity.strip()]
    if not entries:
        raise UsageRuleError("Enter what was used.")
    if len(entries) > MAX_LINES:
        raise UsageRuleError(f"Record at most {MAX_LINES} ingredients at a time.")
    if posted_count(db, org, SOURCE, key):
        return False
    wanted = {use.inventory_item_id for use in entries}
    items = {
        i.id: i
        for i in db.scalars(select_scoped(org, InventoryItem).where(InventoryItem.id.in_(wanted)))
    }
    if wanted - set(items):
        raise NotFound(sorted(wanted - set(items)))
    stocked = {
        row.inventory_item_id
        for row in db.scalars(
            select_scoped(org, ItemLocation).where(
                ItemLocation.location_id == place.id,
                ItemLocation.inventory_item_id.in_(wanted),
            )
        )
    }
    totals: dict[int, Decimal] = defaultdict(Decimal)
    for use in entries:
        item = items[use.inventory_item_id]
        if item.id not in stocked:
            raise UsageRuleError(f"{item.name} is not stocked at {place.name}.")
        try:
            qty = places(entered_base(item, use.quantity, use.unit))
        except ItemRuleError as exc:
            raise UsageRuleError(str(exc)) from exc
        if qty <= 0:
            raise UsageRuleError(f"Enter how much {item.name} was used, above zero.")
        totals[item.id] += qty

    now = now or datetime.now(UTC)
    lines = []
    uncosted = []
    for item_id, qty in sorted(totals.items()):
        cost = cost_on(db, org, place.id, item_id, now)
        if not cost:
            uncosted.append(item_id)
        lines.append(
            Line(
                place.id,
                item_id,
                "manual_consumption",
                -qty,
                unit_cost=cost if cost else None,
                reason=why.text,
            )
        )
    post_transaction(
        db,
        org,
        kind="usage",
        source_type=SOURCE,
        source_id=key,
        idempotency_key=f"usage:{key}",
        lines=lines,
        occurred_at=now,
        reason=why.text,
        refuse_below_zero=not org.can(OVERRIDE),
    )
    for item_id in uncosted:
        record_exception(
            db,
            org,
            Finding(
                type="missing_cost",
                subject_type="item_location",
                subject_id=f"{item_id}:{place.id}",
                title=(
                    f"{items[item_id].name} was used at {place.name} with no cost, so what it "
                    "cost is unknown."
                ),
                location_id=place.id,
            ),
            now=now,
        )
    for item_id in totals:
        after = balance(db, org, place.id, item_id)
        if after < 0:
            record_exception(db, org, negative_finding(items[item_id], place, after), now=now)
    db.flush()
    log.info("usage %s posted at location %s", key, place.id)
    return True


def _windows(
    db: Session, org: OrgContext, since: date, until: date, location_id: int | None
) -> dict[int, tuple[datetime, datetime]]:
    query = select_scoped(org, Location)
    if location_id is not None:
        query = query.where(Location.id == location_id)
    return {
        place.id: (
            business_day_start(since, place.timezone, place.business_day_cutoff_hour),
            business_day_end(until, place.timezone, place.business_day_cutoff_hour),
        )
        for place in db.scalars(query)
    }


@dataclass(frozen=True)
class Used:
    """What left stock of one item at one location over a period, in base units: through
    recorded sales and as manual usage (WF-16 keeps the origins apart)."""

    location_id: int
    inventory_item_id: int
    from_sales: Decimal
    manual: Decimal


def consumption(
    db: Session, org: OrgContext, *, since: date, until: date, location_id: int | None = None
) -> list[Used]:
    """Consumption over business days, per location and item (DR-33): corrected sales and
    reversed records net out."""
    windows = _windows(db, org, since, until, location_id)
    sold: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    manual: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    for m in movements_in(db, org, windows, source_type=SALE_SOURCE):
        sold[(m.location_id, m.inventory_item_id)] -= m.qty_base
    for m in movements_in(db, org, windows, source_type=SOURCE):
        manual[(m.location_id, m.inventory_item_id)] -= m.qty_base
    return [
        Used(
            location,
            item,
            sold.get((location, item), Decimal(0)),
            manual.get((location, item), Decimal(0)),
        )
        for location, item in sorted(set(sold) | set(manual))
        if sold.get((location, item)) or manual.get((location, item))
    ]


@dataclass
class Record:
    """One usage record as the ledger holds it."""

    token: str
    location_id: int
    occurred_at: datetime | None
    recorded_by: str
    reason: str | None
    used: dict[int, Decimal] = field(default_factory=dict)  # item -> base units


def records(
    db: Session, org: OrgContext, *, since: date, until: date, location_id: int | None = None
) -> list[Record]:
    """Usage records over business days, newest first, labelled as manual usage."""
    people = {user.id: user.email for user in members(db, org)}
    found: dict[str, Record] = {}
    for m in movements_in(
        db, org, _windows(db, org, since, until, location_id), source_type=SOURCE
    ):
        if m.source_id not in found:
            found[m.source_id] = Record(
                m.source_id,
                m.location_id,
                m.occurred_at,
                people.get(m.actor_user_id or 0, "A former member")
                if m.actor_user_id
                else "The system",
                m.reason,
            )
        used = found[m.source_id].used
        used[m.inventory_item_id] = used.get(m.inventory_item_id, Decimal(0)) - m.qty_base
    shown = [r for r in found.values() if any(r.used.values())]
    shown.sort(key=lambda r: r.occurred_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return shown
