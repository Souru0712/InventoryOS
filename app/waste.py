"""Waste: logging a loss, voiding it, and reporting it (T2.07; WST-FR-01..12, DR-33, ADR-0058).

The only writer of the waste tables. A waste event is the business record; it posts `waste`
movements to the ledger (an item, or every ingredient of a sellable product by the recipe in
effect then), batch chosen or taken soonest-expiry first, costed at that moment. Every quantity
and cost the log and the report show comes from those movements, never from the event (DR-33).
A void reverses them; within 30 minutes the logger may, afterwards someone allowed to void waste
with a reason (WST-FR-07).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, members, select_scoped
from app.adjustments import negative_finding
from app.audit import record_audit
from app.consumption import resolve_variation
from app.costs import cost_on
from app.exceptions import record_exception
from app.items import entered_base
from app.ledger.history import movements_in
from app.ledger.posting import (
    balance,
    batch_remaining,
    post_transaction,
    reverse_transaction,
    standing,
)
from app.models import (
    InventoryItem,
    ItemLocation,
    Location,
    ProductVariation,
    SalesTransaction,
    SalesTransactionLine,
    StockBatch,
    StockItem,
    Tenant,
    WasteEvent,
    WasteLine,
    WasteReason,
)
from app.transform.business_days import business_date, business_day_end, business_day_start
from app.transform.exceptions import Finding
from app.transform.items import ItemRuleError
from app.transform.ledger import Line, places
from app.transform.waste import (
    DEFAULT_REASONS,
    Loss,
    WasteRuleError,
    check_quantity,
    check_reason_label,
    void_refusal,
)

SOURCE = "waste_event"
OVERRIDE = "adjustment.override_negative"
VOID_ANY = "waste.void"


class NotFound(LookupError):
    """A waste record outside the business, or not there at all."""


# --- reasons (WST-FR-02) --------------------------------------------------------------------------


def reasons(db: Session, org: OrgContext, *, archived: bool = False) -> list[WasteReason]:
    """The business's reasons in order; the defaults until it has its own rows."""
    rows = list(
        db.scalars(select_scoped(org, WasteReason).order_by(WasteReason.position, WasteReason.id))
    )
    return [r for r in rows if archived or r.is_active]


def ensure_reasons(db: Session, org: OrgContext) -> list[WasteReason]:
    """The defaults written down the first time a business needs them (WST-FR-02)."""
    if reasons(db, org, archived=True):
        return reasons(db, org)
    for position, (code, label) in enumerate(DEFAULT_REASONS.items()):
        db.add(WasteReason(tenant_id=org.tenant_id, code=code, label=label, position=position))
    db.flush()
    return reasons(db, org)


def add_reason(db: Session, org: OrgContext, label: str, ip: str | None = None) -> WasteReason:
    name = check_reason_label(label)
    existing = ensure_reasons(db, org)
    if any(r.label.casefold() == name.casefold() for r in reasons(db, org, archived=True)):
        raise WasteRuleError(f"There is already a reason called {name}.")
    row = WasteReason(
        tenant_id=org.tenant_id,
        code=f"custom-{len(reasons(db, org, archived=True)) + 1}",
        label=name,
        position=len(existing) + 1,
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        org,
        "settings.waste_reason_added",
        f"Added the waste reason {name}",
        subject_type="waste_reason",
        subject_id=row.id,
        after={"label": name},
        ip=ip,
    )
    return row


def change_reason(
    db: Session,
    org: OrgContext,
    reason_id: int,
    *,
    label: str | None = None,
    active: bool | None = None,
    ip: str | None = None,
) -> WasteReason:
    """Rename or archive a reason; past waste keeps the name it was logged with."""
    row = get_scoped(db, org, WasteReason, reason_id)
    if row is None:
        raise NotFound(reason_id)
    before = {"label": row.label, "active": row.is_active}
    if label is not None:
        name = check_reason_label(label)
        if any(
            r.id != row.id and r.label.casefold() == name.casefold()
            for r in reasons(db, org, archived=True)
        ):
            raise WasteRuleError(f"There is already a reason called {name}.")
        row.label = name
    if active is not None:
        if not active and len(reasons(db, org)) <= 1 and row.is_active:
            raise WasteRuleError("Keep at least one reason to choose from.")
        row.is_active = active
    after = {"label": row.label, "active": row.is_active}
    if after != before:
        record_audit(
            db,
            org,
            "settings.waste_reason_changed",
            f"Changed the waste reason {row.label}",
            subject_type="waste_reason",
            subject_id=row.id,
            before=before,
            after=after,
            ip=ip,
        )
    db.flush()
    return row


# --- recording and voiding ---------------------------------------------------------------------


def _stocked(db: Session, org: OrgContext, location_id: int, item_ids: set[int]) -> set[int]:
    rows = select_scoped(org, ItemLocation).where(
        ItemLocation.location_id == location_id, ItemLocation.inventory_item_id.in_(item_ids)
    )
    return {row.inventory_item_id for row in db.scalars(rows)}


def _batch_of(db: Session, org: OrgContext, batch_id: int, location_id: int, item_id: int) -> int:
    """A chosen batch, if it is this item's at this location (WST-FR-04)."""
    batch = get_scoped(db, org, StockBatch, batch_id)
    stock = get_scoped(db, org, StockItem, batch.stock_item_id) if batch is not None else None
    if (
        batch is None
        or stock is None
        or stock.location_id != location_id
        or stock.inventory_item_id != item_id
    ):
        raise WasteRuleError("That batch is not this item's here.")
    return batch.id


def record_waste(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    reason_id: int | None,
    quantity: str,
    inventory_item_id: int | None = None,
    product_variation_id: int | None = None,
    unit: str = "",
    batch_id: int | None = None,
    note: str | None = None,
    related_type: str | None = None,
    related_id: str | None = None,
    now: datetime | None = None,
) -> WasteEvent:
    """Log a loss and post it (WST-FR-01..06). Refused with WasteRuleError, or with BelowZero
    when it would take stock below zero and the person may not (DR-08); the caller rolls back."""
    place = get_scoped(db, org, Location, location_id)
    if place is None:
        raise NotFound(location_id)
    reason = get_scoped(db, org, WasteReason, reason_id) if reason_id else None
    if reason_id and reason is None:
        raise NotFound(reason_id)
    if reason is None or not reason.is_active:
        raise WasteRuleError("Choose a reason.")
    if (inventory_item_id is None) == (product_variation_id is None):
        raise WasteRuleError("Choose an item or a product.")
    now = now or datetime.now(UTC)
    variation = None
    recipe_version = None
    if inventory_item_id is not None:
        item = get_scoped(db, org, InventoryItem, inventory_item_id)
        if item is None:
            raise NotFound(inventory_item_id)
        try:
            qty_base = check_quantity(places(entered_base(item, quantity, unit)))
        except ItemRuleError as exc:
            raise WasteRuleError(str(exc)) from exc
        chosen = _batch_of(db, org, batch_id, place.id, item.id) if batch_id else None
        lines = [(item.id, qty_base, chosen)]
        entered, entered_unit = Decimal(quantity.strip()), unit or item.display_unit
    elif product_variation_id is not None:
        variation = get_scoped(db, org, ProductVariation, product_variation_id)
        if variation is None:
            raise NotFound(product_variation_id)
        try:
            entered = check_quantity(Decimal(quantity.strip()))
        except ArithmeticError as exc:
            raise WasteRuleError(f"{quantity} is not a quantity.") from exc
        used = resolve_variation(db, org, variation, entered, now)
        if used.gaps or not used.uses:
            raise WasteRuleError(
                "This product has no recipe in effect, so what it is made of is not known: "
                "log the ingredients instead."
            )
        recipe_version = used.versions[0]
        lines = [(item_id, qty, None) for item_id, qty in sorted(used.uses.items())]
        qty_base, entered_unit = entered, ""
    missing = {item_id for item_id, _, _ in lines} - _stocked(
        db, org, place.id, {item_id for item_id, _, _ in lines}
    )
    if missing:
        names = ", ".join(
            sorted(
                i.name
                for i in db.scalars(
                    select_scoped(org, InventoryItem).where(InventoryItem.id.in_(missing))
                )
            )
        )
        raise WasteRuleError(f"{names} is not stocked at {place.name}.")

    event = WasteEvent(
        tenant_id=org.tenant_id,
        location_id=place.id,
        reason_id=reason.id,
        reason_label=reason.label,
        inventory_item_id=inventory_item_id,
        product_variation_id=product_variation_id,
        recipe_version_id=recipe_version,
        quantity=entered,
        unit=entered_unit[:16],
        qty_base=qty_base,
        note=(note or "").strip()[:300] or None,
        related_type=(related_type or "").strip()[:20] or None,
        related_id=(related_id or "").strip()[:80] or None,
        actor_user_id=org.user_id,
        occurred_at=now,
    )
    db.add(event)
    db.flush()
    why = reason.label + (f": {event.note}" if event.note else "")
    if variation is not None:
        why = f"{variation.product.name} {variation.name} × {places(entered).normalize()}, {why}"
    movements = []
    uncosted = []
    for item_id, qty, chosen in lines:
        db.add(
            WasteLine(
                tenant_id=org.tenant_id,
                waste_event_id=event.id,
                inventory_item_id=item_id,
                qty_base=places(qty),
                batch_id=chosen,
            )
        )
        cost = cost_on(db, org, place.id, item_id, now)
        if not cost:
            uncosted.append(item_id)
        movements.append(
            Line(
                place.id,
                item_id,
                "waste",
                -places(qty),
                unit_cost=cost if cost else None,
                batch_id=chosen,
                reason=why[:300],
            )
        )
    posted = post_transaction(
        db,
        org,
        kind="waste",
        source_type=SOURCE,
        source_id=str(event.id),
        idempotency_key=f"waste:{event.id}",
        lines=movements,
        occurred_at=now,
        reason=why[:300],
        refuse_below_zero=not org.can(OVERRIDE),
    )
    event.transaction_id = posted.id
    items = {
        i.id: i
        for i in db.scalars(
            select_scoped(org, InventoryItem).where(
                InventoryItem.id.in_([item_id for item_id, _, _ in lines])
            )
        )
    }
    for item_id in uncosted:
        record_exception(
            db,
            org,
            Finding(
                type="missing_cost",
                subject_type="item_location",
                subject_id=f"{item_id}:{place.id}",
                title=(
                    f"{items[item_id].name} was wasted at {place.name} with no cost, so the loss "
                    "is unknown."
                ),
                location_id=place.id,
            ),
            now=now,
        )
    for item_id, _, _ in lines:
        after = balance(db, org, place.id, item_id)
        if after < 0:
            record_exception(db, org, negative_finding(items[item_id], place, after), now=now)
    db.flush()
    return event


def void_waste(
    db: Session, org: OrgContext, event_id: int, *, reason: str = "", now: datetime | None = None
) -> WasteEvent:
    """Undo a waste entry with a reversal (WST-FR-07, AC-WST-05)."""
    event = get_scoped(db, org, WasteEvent, event_id)
    if event is None:
        raise NotFound(event_id)
    if event.status == "voided":
        raise WasteRuleError("That entry is already voided.")
    now = now or datetime.now(UTC)
    refusal = void_refusal(
        logged_by=event.actor_user_id,
        recorded_at=event.recorded_at,
        person=org.user_id,
        may_void_any=org.can(VOID_ANY),
        reason=reason,
        now=now,
    )
    if refusal:
        raise WasteRuleError(refusal)
    why = "Waste voided" + (
        f": {reason.strip()}" if reason.strip() else " by the person who logged it"
    )
    for posting in standing(db, org, SOURCE, str(event.id)):
        # At the waste's own moment, so the period it was logged in nets to nothing.
        reverse_transaction(db, org, posting.id, reason=why[:300], occurred_at=posting.occurred_at)
    event.status = "voided"
    event.voided_at = now
    event.voided_by_user_id = org.user_id
    event.void_reason = reason.strip()[:300] or None
    db.flush()
    return event


def recent_entries(
    db: Session, org: OrgContext, *, since: datetime, mine_only: bool = True
) -> list[WasteEvent]:
    """Entries recorded since an instant, newest first: what someone may still void."""
    query = select_scoped(org, WasteEvent).where(WasteEvent.recorded_at >= since)
    if mine_only:
        query = query.where(WasteEvent.actor_user_id == org.user_id)
    return list(db.scalars(query.order_by(WasteEvent.id.desc())))


# --- reading: from the ledger only (DR-33) -----------------------------------------------------


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
class Logged:
    """A waste entry as the log shows it: the event's why and who, the ledger's how much."""

    event: WasteEvent
    losses: list[Loss]

    @property
    def cost_cents(self) -> int | None:
        if any(loss.cost_cents is None for loss in self.losses):
            return None
        return sum((loss.cost_cents or 0) for loss in self.losses)


def logged(
    db: Session,
    org: OrgContext,
    *,
    since: date,
    until: date,
    location_id: int | None = None,
    reason_id: int | None = None,
    item_id: int | None = None,
    actor_id: int | None = None,
) -> list[Logged]:
    """Waste in a period, newest first, each entry with what its ledger entries took (WST-FR-08,
    AC-WST-08). A voided entry nets to nothing and is left out."""
    moved = movements_in(db, org, _windows(db, org, since, until, location_id), source_type=SOURCE)
    net: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    value: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    uncosted: set[tuple[int, int]] = set()
    for m in moved:
        key = (int(m.source_id), m.inventory_item_id)
        net[key] += m.qty_base
        value[key] += m.qty_base * m.unit_cost
        if m.unit_cost == 0:
            uncosted.add(key)
    event_ids = {event_id for event_id, _ in net}
    if not event_ids:
        return []
    events = {
        e.id: e
        for e in db.scalars(select_scoped(org, WasteEvent).where(WasteEvent.id.in_(event_ids)))
    }
    found_items = {
        i.id: i
        for i in db.scalars(
            select_scoped(org, InventoryItem).where(InventoryItem.id.in_({item for _, item in net}))
        )
    }
    people = {user.id: user.email for user in members(db, org)}
    by_event: dict[int, list[Loss]] = defaultdict(list)
    for (event_id, item), qty in net.items():
        event = events.get(event_id)
        if event is None or qty >= 0:
            continue
        if reason_id is not None and event.reason_id != reason_id:
            continue
        if actor_id is not None and event.actor_user_id != actor_id:
            continue
        if item_id is not None and item != item_id:
            continue
        variation = event.product_variation_id
        product = None
        if variation is not None:
            size = get_scoped(db, org, ProductVariation, variation)
            product = f"{size.product.name} {size.name}" if size else None
        by_event[event_id].append(
            Loss(
                event_id,
                event.reason_label,
                found_items[item].name if item in found_items else "item",
                product,
                people.get(event.actor_user_id or 0, "A former member")
                if event.actor_user_id
                else "StockPulse",
                -qty,
                None
                if (event_id, item) in uncosted
                else int((-value[(event_id, item)]).quantize(Decimal(1))),
                found_items[item].display_unit if item in found_items else "",
            )
        )
    return [
        Logged(events[event_id], losses)
        for event_id, losses in sorted(by_event.items(), key=lambda kv: -kv[0])
    ]


@dataclass(frozen=True)
class Short:
    """Stock a count found missing, item by item: loss nobody logged (WST-FR-10)."""

    item: str
    qty_base: Decimal
    cost_cents: int | None
    display_unit: str


def found_short(
    db: Session, org: OrgContext, *, since: date, until: date, location_id: int | None = None
) -> list[Short]:
    """What counts found missing in the period, biggest loss first (AC-WST-07)."""
    moved = movements_in(
        db, org, _windows(db, org, since, until, location_id), movement_type="count_variance"
    )
    qty: dict[int, Decimal] = defaultdict(Decimal)
    value: dict[int, Decimal] = defaultdict(Decimal)
    uncosted: set[int] = set()
    for m in moved:
        if m.qty_base >= 0:
            continue
        qty[m.inventory_item_id] += -m.qty_base
        value[m.inventory_item_id] += -m.qty_base * m.unit_cost
        if m.unit_cost == 0:
            uncosted.add(m.inventory_item_id)
    items = {
        i.id: i
        for i in db.scalars(select_scoped(org, InventoryItem).where(InventoryItem.id.in_(set(qty))))
    }
    found = [
        Short(
            items[item].name if item in items else "item",
            amount,
            None if item in uncosted else int(value[item].quantize(Decimal(1))),
            items[item].display_unit if item in items else "",
        )
        for item, amount in qty.items()
    ]
    return sorted(found, key=lambda s: (-(s.cost_cents or 0), s.item))


# --- disposing of expired stock (T2.08; BAT-FR-10, -11, AC-BAT-07) ------------------------------


def _expired_reason(db: Session, org: OrgContext) -> WasteReason:
    by_code = {r.code: r for r in reasons(db, org, archived=True) or ensure_reasons(db, org)}
    found = by_code.get("expiration")
    if found is None:
        ensure_reasons(db, org)
        found = {r.code: r for r in reasons(db, org, archived=True)}.get("expiration")
    if found is None:
        raise WasteRuleError("The business has no reason for stock past its date.")
    if not found.is_active:
        found.is_active = True  # disposal needs it; the reason list keeps it from here
    return found


def dispose_batch(
    db: Session, org: OrgContext, batch_id: int, *, note: str, now: datetime | None = None
) -> WasteEvent:
    """Write off everything a batch still holds as waste, reason past its date, from that
    batch (BAT-FR-10's dispose action, BAT-FR-11)."""
    batch = get_scoped(db, org, StockBatch, batch_id)
    stock = get_scoped(db, org, StockItem, batch.stock_item_id) if batch is not None else None
    if batch is None or stock is None or stock.inventory_item_id is None:
        raise NotFound(batch_id)
    item = get_scoped(db, org, InventoryItem, stock.inventory_item_id)
    if item is None:
        raise NotFound(batch_id)
    held = batch_remaining(db, org, [batch.id]).get(batch.id, Decimal("0"))
    if held <= 0:
        raise WasteRuleError("That batch holds nothing to dispose of.")
    return record_waste(
        db,
        org,
        location_id=stock.location_id,
        reason_id=_expired_reason(db, org).id,
        inventory_item_id=item.id,
        quantity=str(held),
        unit=item.base_unit,
        batch_id=batch.id,
        note=note,
        now=now,
    )


def dispose_expired(db: Session, org: OrgContext, now: datetime | None = None) -> int:
    """At day close, write off expired prepared batches as waste by the system, if the business
    turned it on (BAT-FR-11, PD-29). A location waits while any of its sales is still waiting to
    be consumed, so the write-off never takes stock a sale already used (AC-BAT-07). Safe to
    run again: a disposed batch holds nothing. Returns how many batches were written off."""
    tenant = get_scoped(db, org, Tenant, org.tenant_id)
    if tenant is None or not tenant.auto_dispose_prepared:
        return 0
    now = now or datetime.now(UTC)
    prepared = {
        i.id
        for i in db.scalars(
            select_scoped(org, InventoryItem).where(InventoryItem.item_type == "prepared")
        )
    }
    if not prepared:
        return 0
    done = 0
    for place in db.scalars(select_scoped(org, Location)):
        waiting = db.scalars(
            select_scoped(org, SalesTransactionLine)
            .join(
                SalesTransaction, SalesTransaction.id == SalesTransactionLine.sales_transaction_id
            )
            .where(
                SalesTransaction.location_id == place.id,
                SalesTransactionLine.consumption_status == "pending",
            )
            .limit(1)
        ).first()
        if waiting is not None:
            continue
        today = business_date(now, place.timezone, place.business_day_cutoff_hour)
        stocks = {
            s.id
            for s in db.scalars(
                select_scoped(org, StockItem).where(
                    StockItem.location_id == place.id,
                    StockItem.inventory_item_id.in_(prepared),
                )
            )
        }
        if not stocks:
            continue
        expired = list(
            db.scalars(
                select_scoped(org, StockBatch).where(
                    StockBatch.stock_item_id.in_(stocks), StockBatch.expires_on < today
                )
            )
        )
        held = batch_remaining(db, org, [b.id for b in expired])
        for batch in expired:
            if held.get(batch.id, Decimal("0")) > 0:
                dispose_batch(
                    db, org, batch.id, note="Written off automatically at day close", now=now
                )
                done += 1
    return done
