"""Onboarding (T2.20; SAAS §5, CMD-FR-11, WF-01).

The checklist: each step is done when the business has what the step makes, read from its own
records, so it never disagrees with the product and a step done any other way counts too. A step
can be skipped and taken back up. Step 0 records how the business takes payments and routes it.
The opening count gives every item at a location its starting quantity on one screen. Onboarding
never blocks anything: a partly set-up business works, and the dashboard shows what is left.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Numeric, cast
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, members, select_scoped
from app.audit import has_event, record_audit
from app.items import entered_base
from app.ledger.posting import has_moved, post_count, post_opening
from app.models import (
    InventoryItem,
    Invitation,
    ItemLocation,
    Location,
    Onboarding,
    ProviderConnection,
    Receiving,
    Recipe,
    SalesSource,
    StockCount,
    StockItem,
    Supplier,
)
from app.transform.items import ItemRuleError
from app.transform.onboarding import (
    ANSWERS,
    OnboardingRuleError,
    check_answer,
    path,
    skipped,
    with_skip,
)

log = logging.getLogger(__name__)


class NotFound(LookupError):
    """No such location for this business."""


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    hint: str
    link: str
    done: bool
    skipped: bool = False


def onboarding_for(db: Session, org: OrgContext) -> Onboarding:
    """The business's onboarding row, made the first time it is read."""
    row = db.scalars(select_scoped(org, Onboarding)).first()
    if row is None:
        row = Onboarding(tenant_id=org.tenant_id, skipped="")
        db.add(row)
        db.flush()
    return row


def answered(db: Session, org: OrgContext) -> bool:
    """Whether step 0 has been answered (read only)."""
    row = db.scalars(select_scoped(org, Onboarding)).first()
    return row is not None and row.payment_answer is not None


def _any(db: Session, query) -> bool:
    return db.scalars(query.limit(1)).first() is not None


def checklist(db: Session, org: OrgContext) -> list[Step]:
    """SAAS §5 steps 2 to 10, in order, each with where to do it."""
    state = db.scalars(select_scoped(org, Onboarding)).first()  # read only: a page never writes
    answer = state.payment_answer if state else None
    away = skipped(state.skipped if state else "")
    connected = path(answer) == "connected"
    file_sales = answer == "pos_unsupported"
    if connected:
        sales = ("Connect your POS", "Square sends each sale as it happens.", "/connect/square")
    elif file_sales:
        sales = ("Choose how sales arrive", "Import your POS's sales file.", "/app/sales/import")
    else:
        sales = (
            "Choose how sales arrive",
            "Enter each day's sales, or import a sales file.",
            "/app/sales/daily",
        )
    people = len(members(db, org))
    steps = [
        (
            "location",
            "Set up your location",
            "Address, time zone and when your business day ends.",
            "/app/locations",
            _any(db, select_scoped(org, Location)),
        ),
        (
            "items",
            "Add your items",
            "Import a CSV, or type them into the quick-add grid.",
            "/app/items/import",
            _any(db, select_scoped(org, InventoryItem)),
        ),
        (
            "suppliers",
            "Add your suppliers",
            "Name, lead time and delivery days.",
            "/app/suppliers",
            _any(db, select_scoped(org, Supplier)),
        ),
        (
            "recipes",
            "Create recipes for your top sellers",
            "What each product uses, so sales take ingredients out of stock.",
            "/app/products",
            _any(db, select_scoped(org, Recipe)),
        ),
        (
            "sales",
            *sales,
            _any(db, select_scoped(org, SalesSource))
            or _any(
                db,
                select_scoped(org, ProviderConnection).where(ProviderConnection.status == "active"),
            ),
        ),
        (
            "alerts",
            "Set your alerts",
            "Reorder points, expiry warnings and when the alert email goes out.",
            "/app/settings/alerts",
            has_event(db, org, ("settings.alerts_changed", "settings.expiry_changed"))
            or _any(
                db,
                select_scoped(org, ItemLocation).where(
                    cast(ItemLocation.reorder_point_base, Numeric) > 0
                ),
            ),
        ),
        (
            "count",
            "Count your stock",
            "One screen gives every item its real starting quantity.",
            "/app/onboarding/count",
            (state is not None and state.opening_counted_at is not None)
            or _any(db, select_scoped(org, StockCount).where(StockCount.status == "posted")),
        ),
        (
            "receiving",
            "Receive a delivery",
            "Check it in against the invoice.",
            "/app/deliveries",
            _any(db, select_scoped(org, Receiving).where(Receiving.status == "posted")),
        ),
        (
            "team",
            "Invite your team",
            "Each person signs in with their own account and role.",
            "/app/team",
            people > 1 or _any(db, select_scoped(org, Invitation)),
        ),
    ]
    return [
        Step(key, title, hint, link, done, key in away and not done)
        for key, title, hint, link, done in steps
    ]


def answer_payments(
    db: Session, org: OrgContext, *, answer: str, pos_name: str = "", ip: str | None = None
) -> Onboarding:
    """Step 0: how the business takes payments today (WF-01). It routes the checklist and keeps
    the POS named (adapter demand) or the wish for one (Scenario C); the plan is not changed."""
    checked, name = check_answer(answer, pos_name)
    row = onboarding_for(db, org)
    was = {"answer": row.payment_answer, "pos": row.pos_name}
    row.payment_answer, row.pos_name = checked, name
    row.answered_at = row.updated_at = datetime.now(UTC)
    record_audit(
        db,
        org,
        "settings.onboarding_answered",
        f"Said how payments are taken: {ANSWERS[checked]}" + (f" ({name})" if name else ""),
        subject_type="tenant",
        subject_id=org.tenant_id,
        before=was,
        after={"answer": checked, "pos": name},
        ip=ip,
    )
    db.flush()
    log.info("tenant %s answered step 0: %s", org.tenant_id, checked)
    return row


def set_skipped(db: Session, org: OrgContext, step: str, skip: bool) -> Onboarding:
    """Skip a step, or take it back up (SAAS §5: any step can be skipped and resumed)."""
    row = onboarding_for(db, org)
    row.skipped = with_skip(row.skipped, step, skip)
    row.updated_at = datetime.now(UTC)
    db.flush()
    return row


@dataclass(frozen=True)
class Counted:
    item_id: int
    opened: bool  # posted as the item's opening balance (it had never moved)
    difference: Decimal  # for an item that had moved: counted minus recorded


def opening_count(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    entries: dict[int, tuple[str, str]],
    now: datetime | None = None,
) -> list[Counted]:
    """The opening count (SAAS §5 step 10): each entered item's quantity at the location. An item
    that never moved there opens with it (`opening_balance`); one that has a history is counted
    against it (a count variance). Items left blank are left alone. The count's time is when
    reliable variance reporting starts."""
    place = get_scoped(db, org, Location, location_id)
    if place is None:
        raise NotFound(location_id)
    now = now or datetime.now(UTC)
    rows = {
        s.inventory_item_id: s
        for s in db.scalars(select_scoped(org, StockItem).where(StockItem.location_id == place.id))
        if s.inventory_item_id is not None
    }
    items = {
        i.id: i
        for i in db.scalars(
            select_scoped(org, InventoryItem).where(InventoryItem.id.in_(list(entries)))
        )
    }
    done: list[Counted] = []
    session = uuid4().hex
    for item_id, (quantity, unit) in entries.items():
        item, stock = items.get(item_id), rows.get(item_id)
        if item is None or stock is None or not quantity.strip():
            continue
        try:
            counted = entered_base(item, quantity, unit)
        except ItemRuleError as exc:
            raise OnboardingRuleError(f"{item.name}: {exc}") from exc
        if counted < 0:
            raise OnboardingRuleError(f"{item.name}: a count cannot be below zero.")
        if not has_moved(db, org, place.id, item.id):
            post_opening(
                db,
                org,
                location_id=place.id,
                inventory_item_id=item.id,
                qty_base=counted,
                source_type="opening_count",
                source_id=session,
                occurred_at=now,
            )
            done.append(Counted(item.id, True, Decimal(0)))
        else:
            before = Decimal(stock.qty_base or 0)
            post_count(
                db,
                org,
                location_id=place.id,
                inventory_item_id=item.id,
                counted=counted,
                source_type="opening_count",
                source_id=session,
                idempotency_key=f"opening_count:{session}:{item.id}",
                occurred_at=now,
                reason="Opening count",
            )
            done.append(Counted(item.id, False, counted - before))
        stock.last_counted_at = now
    if done:
        onboarding_for(db, org).opening_counted_at = now
    db.flush()
    log.info("opening count of %d items at location %s", len(done), place.id)
    return done
