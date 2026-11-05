"""Paid snack boxes: allocation against the till's count, packing, pickup, refunds and reconciliation.

Square is the till's authority. It has no reserved state, so a paid box sells its units out of
IN_STOCK straight away and Square's count stays exactly what walk-in customers may still buy.
This module owns which box holds which units, and keeps the two in step when a push fails or the
till sells a unit that a paid box was counting on.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.load.upsert import apply_provider_counts
from app.models import (
    BoxLine,
    BoxLineStatus,
    BoxOrder,
    BoxOrderStatus,
    BoxReview,
    BoxSettings,
    Customer,
    InventoryEvent,
    Location,
    SnackProfile,
    StockItem,
    TrackMode,
    WebhookEvent,
)
from app.orchestration import customers, referrals
from app.orchestration.sync import NotConnected, _access_token, active_connection
from app.orchestration.views import expiry_positions
from app.providers.square.client import SquareClient, state_change
from app.transform.allocation import Allocation, Position, check_transition, position, short_lines
from app.transform.boxes import (
    BOX_TYPES,
    FLAVORS,
    Candidate,
    Pick,
    Rules,
    build_box,
    cogs_ceiling_cents,
    contribution_cents,
    suggest_replacements,
)
from app.transform.taste import crowd_quality
from app.units import Dimension, from_base

log = logging.getLogger(__name__)
ZERO = Decimal("0")
LIVE_LINES = (BoxLineStatus.ALLOCATED, BoxLineStatus.PACKED)
# Orders whose lines still hold stock in the store.
HOLDING = (BoxOrderStatus.PAID, BoxOrderStatus.PACKED)
PRICE_FIELDS = {
    "sweet": "price_sweet_cents",
    "chaos": "price_chaos_cents",
    "world": "price_world_cents",
}
SETTING_FIELDS = (
    "price_sweet_cents",
    "price_chaos_cents",
    "price_world_cents",
    "min_items",
    "max_items",
    "target_cogs_cents",
    "packaging_cents",
    "payment_fee_bps",
    "payment_fee_fixed_cents",
    "min_contribution_cents",
    "min_days_after_pickup",
    "pickup_after_days",
    "referral_reward_cents",
    "referral_discount_cents",
    "payment_window_minutes",
)


class BoxProblem(ValueError):
    """Something that stops an order before anything has changed. The message says what to do."""


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def now_utc() -> datetime:
    return datetime.now(UTC)


def tags(text: str | None) -> frozenset[str]:
    return frozenset(t for t in (text or "").split(";") if t)


# --- settings ---------------------------------------------------------------------------------


def box_settings(db: Session, tenant_id: int) -> BoxSettings:
    row = db.scalars(select(BoxSettings).where(BoxSettings.tenant_id == tenant_id)).first()
    if row is None:
        row = BoxSettings(tenant_id=tenant_id)
        db.add(row)
        db.flush()
    return row


def rules_hash(settings: BoxSettings) -> str:
    """Short fingerprint of the terms a box was built under, so a later change is traceable."""
    terms = {field: getattr(settings, field) for field in SETTING_FIELDS}
    return hashlib.sha1(
        json.dumps(terms, sort_keys=True).encode(), usedforsecurity=False
    ).hexdigest()[:8]


def update_settings(
    db: Session,
    settings: BoxSettings,
    values: dict[str, int],
    is_open: bool,
    location_id: int | None,
) -> list[str]:
    problems = []
    if any(values.get(f, 1) <= 0 for f in PRICE_FIELDS.values()):
        problems.append("Every box price has to be above zero.")
    if values.get("min_items", 1) < 1 or values.get("min_items", 1) > values.get("max_items", 1):
        problems.append("Minimum items has to be at least 1 and no more than the maximum.")
    if any(values.get(f, 0) < 0 for f in SETTING_FIELDS):
        problems.append("None of these can be negative.")
    if values.get("payment_window_minutes", 60) < 5:
        problems.append("Give customers at least 5 minutes to pay.")
    cheapest = min(values.get(f, 1) for f in PRICE_FIELDS.values())
    if values.get("referral_discount_cents", 0) >= cheapest:
        problems.append("The referral discount has to be less than the cheapest box.")
    if location_id is not None:
        location = db.get(Location, location_id)
        if location is None or location.tenant_id != settings.tenant_id:
            problems.append("Choose one of your own locations.")
    if problems:
        return problems
    for field in SETTING_FIELDS:
        if field in values:
            setattr(settings, field, int(values[field]))
    settings.is_open = is_open
    settings.location_id = location_id
    settings.updated_at = now_utc()
    db.flush()
    return []


def pickup_location(db: Session, settings: BoxSettings) -> Location | None:
    if settings.location_id:
        return db.get(Location, settings.location_id)
    return db.scalars(
        select(Location)
        .where(Location.tenant_id == settings.tenant_id, Location.is_active.is_(True))
        .limit(1)
    ).first()


def _rules(settings: BoxSettings, order: BoxOrder) -> Rules:
    # The referral discount is this order's acquisition cost, so it shrinks the snack budget. Store
    # credit does not: it was booked when earned, and spending it should not buy a smaller box.
    ceiling = cogs_ceiling_cents(
        order.price_cents - order.discount_cents,
        settings.packaging_cents,
        settings.payment_fee_bps,
        settings.payment_fee_fixed_cents,
        settings.min_contribution_cents,
    )
    return Rules(
        min_items=settings.min_items,
        max_items=settings.max_items,
        target_cogs_cents=Decimal(settings.target_cogs_cents),
        cogs_ceiling_cents=ceiling,
        min_days_after_pickup=settings.min_days_after_pickup + settings.pickup_after_days,
    )


def order_contribution_cents(db: Session, settings: BoxSettings, order: BoxOrder) -> Decimal:
    cogs = sum(
        (
            Decimal(line.unit_cost_cents) * Decimal(line.qty_base)
            for line in db.scalars(select(BoxLine).where(BoxLine.box_order_id == order.id))
            if line.status is not BoxLineStatus.RELEASED
        ),
        ZERO,
    )
    return contribution_cents(
        order.price_cents - order.discount_cents,
        cogs,
        settings.packaging_cents,
        settings.payment_fee_bps,
        settings.payment_fee_fixed_cents,
    )


# --- Square seams (tests replace these) --------------------------------------------------------


def square_move(
    db: Session,
    tenant_id: int,
    item: StockItem,
    from_state: str,
    to_state: str,
    qty_base: Decimal,
    key: str,
) -> bool:
    """One inventory state change in Square. False when Square does not own this item's count."""
    if item.track_mode is not TrackMode.PROVIDER or not item.provider_variation_id:
        return False
    location = db.get(Location, item.location_id)
    connection = active_connection(db, tenant_id)
    change = state_change(
        item.provider_variation_id,
        location.provider_location_id,
        str(from_base(qty_base, item.stock_unit)),
        now_utc(),
        from_state,
        to_state,
    )
    with SquareClient(_access_token(db, connection)) as client:
        client.apply_inventory_changes([change], key)
    return True


def square_refresh(db: Session, tenant_id: int, items: list[StockItem]) -> bool:
    """Re-read Square's counts for these items in one call. False when that was not possible."""
    from app.transform.square_normalize import normalize_counts

    owned = [i for i in items if i.track_mode is TrackMode.PROVIDER and i.provider_variation_id]
    if not owned:
        return True
    try:
        connection = active_connection(db, tenant_id)
        locations = {
            loc.id: loc.provider_location_id
            for loc in db.scalars(select(Location).where(Location.tenant_id == tenant_id))
        }
        with SquareClient(_access_token(db, connection)) as client:
            raw = client.inventory_counts(
                [i.provider_variation_id for i in owned],
                sorted({locations[i.location_id] for i in owned if i.location_id in locations}),
            )
        apply_provider_counts(db, tenant_id, normalize_counts(raw))
        return True
    except (
        Exception
    ) as exc:  # offline, token expired, Square down: all mean the counts are unconfirmed
        log.warning("could not refresh square counts for tenant %s: %s", tenant_id, exc)
        return False


def square_checkout(db: Session, order: BoxOrder, customer: Customer) -> tuple[str, str] | None:
    """A hosted Square checkout for the amount due, as (url, square order id). None without Square."""
    location = db.get(Location, order.location_id)
    if location is None or location.provider != "square":
        return None
    try:
        connection = active_connection(db, order.tenant_id)
    except NotConnected:
        return None
    base = get_settings().app_base_url.rstrip("/")
    with SquareClient(_access_token(db, connection)) as client:
        link = client.create_payment_link(
            idempotency_key=f"box-order:{order.id}",
            name=f"{order.box_type.title()} snack box",
            amount_cents=order.amount_due_cents,
            currency=location.currency,
            location_id=location.provider_location_id,
            redirect_url=f"{base}/shop/{order.tenant_id}/me/orders/{order.id}?paid=1",
            note=f"Snack box order {order.id}",
            buyer_email=customer.email,
        )
    if not link.get("url") or not link.get("order_id"):
        raise RuntimeError("Square returned a payment link without a url or order id")
    return link["url"], link["order_id"]


# --- the count ---------------------------------------------------------------------------------


def _event(
    db: Session,
    item: StockItem,
    order: BoxOrder,
    action: str,
    delta: Decimal | None,
    pushed: bool,
    detail: str,
) -> None:
    db.add(
        InventoryEvent(
            tenant_id=item.tenant_id,
            stock_item_id=item.id,
            action=action,
            source="snack_box",
            delta_base=delta,
            qty_after_base=item.qty_base,
            detail=detail[:500],
            pushed_to_pos=pushed,
            box_order_id=order.id,
        )
    )


def _take(db: Session, order: BoxOrder, line: BoxLine, item: StockItem) -> bool:
    """Take a paid line's units out of the count the till sells from. False when Square was not told."""
    if line.count_applied:
        return True
    pushed = False
    if item.track_mode is TrackMode.PROVIDER and item.provider_variation_id:
        try:
            pushed = square_move(
                db,
                order.tenant_id,
                item,
                "IN_STOCK",
                "SOLD",
                line.qty_base,
                f"box-line-sold:{line.id}",
            )
        except Exception as exc:
            log.warning("could not sell box line %s out of square: %s", line.id, exc)
            return False
    item.qty_base = Decimal(item.qty_base) - Decimal(line.qty_base)
    line.count_applied = True
    _event(
        db, item, order, "box_allocated", -Decimal(line.qty_base), pushed, f"box order {order.id}"
    )
    return True


def _give_back(db: Session, order: BoxOrder, line: BoxLine, item: StockItem, action: str) -> bool:
    """Return a line's units to the count. False when Square was not told; reconcile retries it."""
    if not line.count_applied:
        return True
    pushed = False
    if item.track_mode is TrackMode.PROVIDER and item.provider_variation_id:
        try:
            pushed = square_move(
                db,
                order.tenant_id,
                item,
                "NONE",
                "IN_STOCK",
                line.qty_base,
                f"box-line-return:{line.id}",
            )
        except Exception as exc:
            log.warning("could not return box line %s to square: %s", line.id, exc)
            return False
    item.qty_base = Decimal(item.qty_base) + Decimal(line.qty_base)
    line.count_applied = False
    _event(db, item, order, action, Decimal(line.qty_base), pushed, f"box order {order.id}")
    return True


def _unpushed_note(names: list[str]) -> str | None:
    if not names:
        return None
    return (
        f"Square was not told about {', '.join(sorted(set(names)))} - the till may still sell "
        "them. Reconcile retries automatically."
    )


def item_positions(db: Session, tenant_id: int, item_ids) -> dict[int, Position]:
    """Each item's count against the box lines that hold its units or that the count is wrong about."""
    item_ids = set(item_ids)
    if not item_ids:
        return {}
    allocations: dict[int, list[Allocation]] = defaultdict(list)
    for line, order in db.execute(
        select(BoxLine, BoxOrder)
        .join(BoxOrder, BoxOrder.id == BoxLine.box_order_id)
        .where(
            BoxLine.tenant_id == tenant_id,
            BoxLine.stock_item_id.in_(item_ids),
            BoxLine.status.in_(LIVE_LINES),
        )
    ).all():
        # A collected box no longer holds stock, unless Square was never told its units left.
        if order.status not in HOLDING and line.count_applied:
            continue
        allocations[line.stock_item_id].append(
            Allocation(
                line.id,
                order.id,
                aware(order.paid_at) or aware(order.created_at),
                Decimal(line.qty_base),
                line.count_applied,
                line.status is BoxLineStatus.PACKED,
            )
        )
    items = {i.id: i for i in db.scalars(select(StockItem).where(StockItem.id.in_(item_ids)))}
    return {
        i: position(Decimal(items[i].qty_base or 0), allocations.get(i, []))
        for i in item_ids
        if i in items
    }


def _settle_shortages(db: Session, tenant_id: int, item_ids, now: datetime) -> list[BoxOrder]:
    """Lines whose units the till sold after payment go short, newest paid first. Returns those orders."""
    touched: dict[int, BoxOrder] = {}
    for item_id, pos in item_positions(db, tenant_id, item_ids).items():
        for line_id in short_lines(pos):
            line = db.get(BoxLine, line_id)
            order = db.get(BoxOrder, line.box_order_id)
            item = db.get(StockItem, item_id)
            if (
                order.status is not BoxOrderStatus.PAID
                or line.status is not BoxLineStatus.ALLOCATED
            ):
                continue
            # The walk-in customer has the unit now; whatever this box took out of the count comes back.
            if not _give_back(db, order, line, item, "box_short"):
                continue
            line.status = BoxLineStatus.SHORT
            order.attention = (
                f"{item.name} was sold at the till after this box was paid - choose a replacement."
            )
            touched[order.id] = order
    db.flush()
    return list(touched.values())


# --- building ----------------------------------------------------------------------------------


def candidates(db: Session, tenant_id: int, location_id: int, today) -> list[Candidate]:
    """Every boxable item at the location, with what walk-ins have not already claimed of it."""
    profiles = {
        p.stock_item_id: p
        for p in db.scalars(
            select(SnackProfile).where(
                SnackProfile.tenant_id == tenant_id, SnackProfile.boxable.is_(True)
            )
        )
    }
    if not profiles:
        return []
    items = list(
        db.scalars(
            select(StockItem).where(
                StockItem.tenant_id == tenant_id,
                StockItem.id.in_(profiles),
                StockItem.is_active.is_(True),
                StockItem.location_id == location_id,
                StockItem.dimension == Dimension.COUNT,
            )
        )
    )
    expiry = {e.item.id: e for e in expiry_positions(db, tenant_id, today, location_id)}
    positions = item_positions(db, tenant_id, [i.id for i in items])
    quality = crowd_quality(
        (r.stock_item_id, r.rating)
        for r in db.scalars(select(BoxReview).where(BoxReview.tenant_id == tenant_id))
    )
    result = []
    for item in items:
        profile, dated = profiles[item.id], expiry.get(item.id)
        live = [
            p
            for p in (dated.positions if dated else [])
            if p.remaining_base > ZERO and p.days_left >= 0
        ]
        # Undated units are treated as the oldest on the shelf, so the unit a box gets could be one.
        days_left = (
            None
            if dated is None or dated.undated_base > ZERO
            else min((p.days_left for p in live), default=None)
        )
        result.append(
            Candidate(
                stock_item_id=item.id,
                name=item.name,
                category=item.category or "",
                origin=profile.origin,
                flavors=tags(profile.flavors),
                allergens=None if profile.allergens is None else tags(profile.allergens),
                unit_cost_cents=Decimal(item.cost_per_base_cents or 0),
                available_base=positions[item.id].store_available_base,
                days_left=days_left,
                at_risk_base=sum((p.projected_leftover_base or ZERO for p in live), ZERO),
                quality=quality.get(item.id, 0.0),
            )
        )
    return result


def _allocate(db: Session, order: BoxOrder, settings: BoxSettings, now: datetime) -> None:
    customer = db.get(Customer, order.customer_id)
    if customer.allergens is None:
        order.attention = (
            "The customer never answered the allergy question, so nothing was allocated."
        )
        return
    boxable = list(
        db.scalars(
            select(StockItem)
            .join(SnackProfile, SnackProfile.stock_item_id == StockItem.id)
            .where(
                StockItem.tenant_id == order.tenant_id,
                SnackProfile.boxable.is_(True),
                StockItem.location_id == order.location_id,
            )
        )
    )
    fresh = square_refresh(db, order.tenant_id, boxable)
    picks, reason = build_box(
        candidates(db, order.tenant_id, order.location_id, now.date()),
        customers.taste_for(db, customer),
        order.box_type,
        _rules(settings, order),
    )
    if not picks:
        order.attention = (
            f"Could not build this box: {reason}. Mark more stock boxable, or refund it."
        )
        return
    unpushed = []
    for pick in picks:
        line = BoxLine(
            tenant_id=order.tenant_id,
            box_order_id=order.id,
            stock_item_id=pick.candidate.stock_item_id,
            qty_base=Decimal("1"),
            unit_cost_cents=pick.candidate.unit_cost_cents,
            fit=Decimal(str(pick.fit)),
            shelf=Decimal(str(pick.shelf)),
            driver=pick.driver,
            why=pick.why[:500],
            created_at=now,
        )
        db.add(line)
        db.flush()
        if not _take(db, order, line, db.get(StockItem, line.stock_item_id)):
            unpushed.append(pick.candidate.name)
    notes = [
        n
        for n in (
            _unpushed_note(unpushed),
            None if fresh else "Built from the last synced counts - Square could not be read.",
        )
        if n
    ]
    order.attention = " ".join(notes) or None
    order.rules = rules_hash(settings)


# --- lifecycle ---------------------------------------------------------------------------------


def place_order(
    db: Session,
    tenant_id: int,
    customer: Customer,
    box_type: str,
    referral_code: str = "",
    use_credit: bool = True,
    now: datetime | None = None,
) -> BoxOrder:
    """An unpaid order with its discount and credit applied. Raises BoxProblem or ReferralProblem."""
    now = now or now_utc()
    settings = box_settings(db, tenant_id)
    if not settings.is_open:
        raise BoxProblem("Boxes are not open for orders right now.")
    if box_type not in BOX_TYPES:
        raise BoxProblem("Pick one of the box types.")
    # Refused here, not discovered after payment when the box cannot be built.
    if "spicy" in BOX_TYPES[box_type].require_flavors and customer.spice == "none":
        raise BoxProblem(
            f"A {box_type.title()} box always has something spicy - pick another box, "
            "or change your spice setting."
        )
    if customer.tenant_id != tenant_id or not customer.is_active:
        raise BoxProblem("Sign in again to order.")
    if customer.allergens is None or customer.allergens_confirmed_at is None:
        raise BoxProblem("Tell us about allergies first - even if the answer is none.")
    location = pickup_location(db, settings)
    if location is None:
        raise BoxProblem("This store has not set a pickup location yet.")

    order = BoxOrder(
        tenant_id=tenant_id,
        location_id=location.id,
        customer_id=customer.id,
        box_type=box_type,
        price_cents=getattr(settings, PRICE_FIELDS[box_type]),
        pay_by=now + timedelta(minutes=settings.payment_window_minutes),
        created_at=now,
        rules=rules_hash(settings),
    )
    db.add(order)
    db.flush()
    if referral_code.strip():
        referrals.apply_code(db, order, customer, referral_code, settings)
    if use_credit:
        referrals.redeem(db, order, customer)
    if order.amount_due_cents == 0:
        confirm_payment(db, order, "credit", now=now)
    db.flush()
    return order


def confirm_payment(
    db: Session,
    order: BoxOrder,
    source: str,
    payment_id: str | None = None,
    now: datetime | None = None,
) -> BoxOrder:
    """Money is in: build the box and take its units out of the till's count. Safe to call twice."""
    now = now or now_utc()
    if order.status is BoxOrderStatus.CANCELLED:
        order.attention = (
            "Paid after it was cancelled for non-payment. Refund it in Square, "
            "or take the order again at the counter."
        )
        db.flush()
        return order
    if order.status is not BoxOrderStatus.PENDING_PAYMENT:
        return order
    check_transition(order.status.value, "paid")
    settings = box_settings(db, order.tenant_id)
    order.status = BoxOrderStatus.PAID
    order.paid_at = now
    order.payment_source = source
    order.square_payment_id = payment_id or order.square_payment_id
    order.pickup_on = now.date() + timedelta(days=settings.pickup_after_days)
    _allocate(db, order, settings, now)
    db.flush()
    return order


def pack(
    db: Session, order: BoxOrder, ticked_line_ids: set[int], now: datetime | None = None
) -> list[str]:
    """Check the shelf again, then close the box. Problems come back as a list; nothing is raised."""
    now = now or now_utc()
    if order.status is not BoxOrderStatus.PAID:
        return [
            f"Only a paid box can be packed; this one is {order.status.value.replace('_', ' ')}."
        ]
    lines = list(
        db.scalars(
            select(BoxLine).where(
                BoxLine.box_order_id == order.id,
                BoxLine.status.in_([BoxLineStatus.ALLOCATED, BoxLineStatus.SHORT]),
            )
        )
    )
    if not lines:
        return ["This box has nothing in it yet."]
    items = {line.stock_item_id: db.get(StockItem, line.stock_item_id) for line in lines}

    # The shelf decides, not the last sync: read Square again right before anything leaves it.
    if not square_refresh(db, order.tenant_id, list(items.values())):
        return ["Square could not be read, so packing is paused. Try again in a minute."]
    for line in lines:
        if line.status is BoxLineStatus.ALLOCATED:
            _take(db, order, line, items[line.stock_item_id])
    _settle_shortages(db, order.tenant_id, list(items), now)
    short = [line for line in lines if line.status is BoxLineStatus.SHORT]
    if short:
        return [f"{len(short)} item(s) were sold at the till - replace them before packing."]
    unticked = [line for line in lines if line.id not in ticked_line_ids]
    if unticked:
        return [f"Tick every item as it goes in the box ({len(unticked)} not ticked)."]

    for line in lines:
        line.status = BoxLineStatus.PACKED
        _event(
            db, items[line.stock_item_id], order, "box_packed", None, False, f"box order {order.id}"
        )
    check_transition(order.status.value, "packed")
    order.status = BoxOrderStatus.PACKED
    order.packed_at = now
    order.attention = _unpushed_note(
        [items[line.stock_item_id].name for line in lines if not line.count_applied]
    )
    db.flush()
    return []


def pick_up(db: Session, order: BoxOrder, now: datetime | None = None) -> list[str]:
    now = now or now_utc()
    if order.status is not BoxOrderStatus.PACKED:
        return ["Only a packed box can be picked up."]
    check_transition(order.status.value, "picked_up")
    order.status = BoxOrderStatus.PICKED_UP
    order.picked_up_at = now
    referrals.qualify_for_order(db, order, now)
    db.flush()
    return []


def cancel(db: Session, order: BoxOrder, now: datetime | None = None) -> list[str]:
    """Cancel an order that was never paid. A paid one is refunded instead."""
    now = now or now_utc()
    if order.status is not BoxOrderStatus.PENDING_PAYMENT:
        return ["Only an unpaid order can be cancelled; refund a paid one."]
    check_transition(order.status.value, "cancelled")
    order.status = BoxOrderStatus.CANCELLED
    order.cancelled_at = now
    referrals.return_redemption(db, order)
    referrals.unwind_for_order(db, order, now)
    db.flush()
    return []


def refund(db: Session, order: BoxOrder, restock: bool, now: datetime | None = None) -> list[str]:
    """Give the money back. Unpacked units always return to the shelf; packed ones only when restocked."""
    now = now or now_utc()
    if order.status is BoxOrderStatus.REFUNDED:
        return []
    if order.status not in (BoxOrderStatus.PAID, BoxOrderStatus.PACKED, BoxOrderStatus.PICKED_UP):
        return ["Only a paid box can be refunded; cancel an unpaid one."]
    unpacked = order.status is BoxOrderStatus.PAID
    problems = []
    for line in db.scalars(
        select(BoxLine).where(
            BoxLine.box_order_id == order.id, BoxLine.status != BoxLineStatus.RELEASED
        )
    ):
        if not (unpacked or restock or line.status is BoxLineStatus.SHORT):
            continue
        item = db.get(StockItem, line.stock_item_id)
        if not _give_back(db, order, line, item, "box_refunded"):
            problems.append(
                f"Square was not told {item.name} is back on the shelf; reconcile retries it."
            )
        line.status = BoxLineStatus.RELEASED
        line.released_at = now
    check_transition(order.status.value, "refunded")
    order.status = BoxOrderStatus.REFUNDED
    order.refunded_at = now
    order.attention = " ".join(problems) or None
    referrals.return_redemption(db, order)
    referrals.unwind_for_order(db, order, now)
    db.flush()
    return problems


def restock(db: Session, order: BoxOrder, now: datetime | None = None) -> list[str]:
    """Put a refunded, packed box back on the shelf. Only for snacks that can safely be sold again."""
    now = now or now_utc()
    if order.status is not BoxOrderStatus.REFUNDED:
        return ["Only a refunded box can be restocked."]
    problems = []
    for line in db.scalars(
        select(BoxLine).where(
            BoxLine.box_order_id == order.id, BoxLine.status == BoxLineStatus.PACKED
        )
    ):
        item = db.get(StockItem, line.stock_item_id)
        if not _give_back(db, order, line, item, "box_restocked"):
            problems.append(
                f"Square was not told {item.name} is back on the shelf; reconcile retries it."
            )
        line.status = BoxLineStatus.RELEASED
        line.released_at = now
    order.attention = " ".join(problems) or None
    db.flush()
    return problems


def expire_unpaid(db: Session, tenant_id: int, now: datetime | None = None) -> int:
    """Cancel orders nobody paid for in time, returning any credit they had put on them."""
    now = now or now_utc()
    due = list(
        db.scalars(
            select(BoxOrder).where(
                BoxOrder.tenant_id == tenant_id,
                BoxOrder.status == BoxOrderStatus.PENDING_PAYMENT,
                BoxOrder.pay_by < now,
            )
        )
    )
    for order in due:
        cancel(db, order, now)
    return len(due)


# --- replacing a line ----------------------------------------------------------------------------


def _pick_for_line(db: Session, line: BoxLine) -> Pick:
    item = db.get(StockItem, line.stock_item_id)
    profile = db.scalars(select(SnackProfile).where(SnackProfile.stock_item_id == item.id)).first()
    candidate = Candidate(
        stock_item_id=item.id,
        name=item.name,
        category=item.category or "",
        origin=profile.origin if profile else "",
        flavors=tags(profile.flavors if profile else ""),
        allergens=tags(profile.allergens) if profile and profile.allergens is not None else None,
        unit_cost_cents=Decimal(line.unit_cost_cents),
        available_base=ZERO,
        days_left=None,
    )
    return Pick(candidate, float(line.fit), float(line.shelf), line.driver, line.why)


def replacement_options(
    db: Session, order: BoxOrder, line: BoxLine, limit: int = 3, now: datetime | None = None
) -> list[Pick]:
    now = now or now_utc()
    settings = box_settings(db, order.tenant_id)
    customer = db.get(Customer, order.customer_id)
    lines = list(db.scalars(select(BoxLine).where(BoxLine.box_order_id == order.id)))
    # Something already taken out of this box was taken out for a reason; it is not offered back.
    released = {bl.stock_item_id for bl in lines if bl.status is BoxLineStatus.RELEASED}
    pool = [
        c
        for c in candidates(db, order.tenant_id, order.location_id, now.date())
        if c.stock_item_id not in released
    ]
    current = [
        _pick_for_line(db, bl)
        for bl in lines
        if bl.status in (BoxLineStatus.ALLOCATED, BoxLineStatus.SHORT)
    ]
    return suggest_replacements(
        pool,
        customers.taste_for(db, customer),
        order.box_type,
        _rules(settings, order),
        current,
        line.stock_item_id,
        limit,
    )


def replace_line(
    db: Session, order: BoxOrder, line: BoxLine, stock_item_id: int, now: datetime | None = None
) -> list[str]:
    now = now or now_utc()
    if order.status is not BoxOrderStatus.PAID:
        return ["Items can only be swapped before the box is packed."]
    if line.box_order_id != order.id or line.status not in (
        BoxLineStatus.ALLOCATED,
        BoxLineStatus.SHORT,
    ):
        return ["That item is not in this box any more."]
    offer = {
        p.candidate.stock_item_id: p for p in replacement_options(db, order, line, 500, now)
    }.get(stock_item_id)
    if offer is None:
        return [
            "That item cannot go in this box (allergens, stock, date or budget). Choose a suggestion."
        ]
    old = db.get(StockItem, line.stock_item_id)
    if not _give_back(db, order, line, old, "box_swapped_out"):
        return [f"Square could not be told {old.name} is back, so nothing was swapped. Try again."]
    line.status = BoxLineStatus.RELEASED
    line.released_at = now
    new = BoxLine(
        tenant_id=order.tenant_id,
        box_order_id=order.id,
        stock_item_id=stock_item_id,
        qty_base=Decimal("1"),
        unit_cost_cents=offer.candidate.unit_cost_cents,
        fit=Decimal(str(offer.fit)),
        shelf=Decimal(str(offer.shelf)),
        driver="packer",
        why=f"swapped in for {old.name}; {offer.why}"[:500],
        created_at=now,
    )
    db.add(new)
    db.flush()
    problems = (
        []
        if _take(db, order, new, db.get(StockItem, stock_item_id))
        else [f"Square was not told about {offer.candidate.name}; reconcile retries it."]
    )
    still_short = db.scalars(
        select(BoxLine.id).where(
            BoxLine.box_order_id == order.id, BoxLine.status == BoxLineStatus.SHORT
        )
    ).first()
    if still_short is None:
        order.attention = " ".join(problems) or None
    db.flush()
    return problems


# --- reconciliation ------------------------------------------------------------------------------


@dataclass(frozen=True)
class StockRow:
    item: StockItem
    position: Position
    issues: tuple[str, ...]


def stock_report(
    db: Session, tenant_id: int, item_ids=None, counts_fresh: bool = True
) -> list[StockRow]:
    if item_ids is None:
        item_ids = {
            line.stock_item_id
            for line in db.scalars(
                select(BoxLine).where(
                    BoxLine.tenant_id == tenant_id,
                    BoxLine.status.in_(LIVE_LINES + (BoxLineStatus.SHORT,)),
                )
            )
        }
    rows = []
    for item_id, pos in item_positions(db, tenant_id, item_ids).items():
        issues = (
            [] if counts_fresh else ["Square could not be read; these are the last synced counts"]
        )
        if pos.count_base < 0:
            issues.append("the till's count is below zero")
        if pos.unapplied_base > 0:
            issues.append(f"{int(pos.unapplied_base)} box unit(s) not yet taken out of Square")
        if pos.store_available_base < 0:
            issues.append("the till has sold units that belong to paid boxes")
        rows.append(StockRow(db.get(StockItem, item_id), pos, tuple(issues)))
    return sorted(rows, key=lambda r: (not r.issues, r.item.name))


def reconcile(db: Session, tenant_id: int, now: datetime | None = None) -> list[StockRow]:
    """Retry every push Square missed, settle what the till oversold, and report what still disagrees."""
    now = now or now_utc()
    rows = db.execute(
        select(BoxLine, BoxOrder)
        .join(BoxOrder, BoxOrder.id == BoxLine.box_order_id)
        .where(
            BoxLine.tenant_id == tenant_id,
            BoxOrder.status != BoxOrderStatus.CANCELLED,
        )
    ).all()
    pending_take = [(bl, o) for bl, o in rows if bl.status in LIVE_LINES and not bl.count_applied]
    pending_return = [
        (bl, o)
        for bl, o in rows
        if bl.status in (BoxLineStatus.RELEASED, BoxLineStatus.SHORT) and bl.count_applied
    ]
    live = [(bl, o) for bl, o in rows if bl.status in LIVE_LINES and o.status in HOLDING]
    item_ids = {bl.stock_item_id for bl, _ in pending_take + pending_return + live}
    items = {i: db.get(StockItem, i) for i in item_ids}

    fresh = square_refresh(db, tenant_id, list(items.values()))
    for line, order in pending_take:
        _take(db, order, line, items[line.stock_item_id])
    for line, order in pending_return:
        _give_back(db, order, line, items[line.stock_item_id], "box_returned_late")
    _settle_shortages(db, tenant_id, item_ids, now)

    for order in {
        o.id: o for _, o in rows if o.attention and o.attention.startswith("Square was not told")
    }.values():
        missing = db.scalars(
            select(BoxLine.id).where(
                BoxLine.box_order_id == order.id,
                BoxLine.status.in_(LIVE_LINES),
                BoxLine.count_applied.is_(False),
            )
        ).first()
        if missing is None:
            order.attention = None
    db.flush()
    return stock_report(db, tenant_id, item_ids, fresh)


# --- Square webhooks -----------------------------------------------------------------------------


def seen_before(
    db: Session, provider: str, event_id: str, event_type: str, tenant_id: int | None
) -> bool:
    """Record a provider event. True when it was already recorded, so its effects must not run twice."""
    if not event_id:
        return False
    if db.scalars(
        select(WebhookEvent.id).where(
            WebhookEvent.provider == provider, WebhookEvent.event_id == event_id
        )
    ).first():
        return True
    db.add(
        WebhookEvent(
            provider=provider, event_id=event_id, event_type=event_type[:80], tenant_id=tenant_id
        )
    )
    db.flush()
    return False


def _order_for_square(
    db: Session, tenant_id: int, square_order_id: str | None, payment_id: str | None
) -> BoxOrder | None:
    if square_order_id:
        order = db.scalars(
            select(BoxOrder).where(
                BoxOrder.tenant_id == tenant_id, BoxOrder.square_order_id == square_order_id
            )
        ).first()
        if order is not None:
            return order
    if payment_id:
        return db.scalars(
            select(BoxOrder).where(
                BoxOrder.tenant_id == tenant_id, BoxOrder.square_payment_id == payment_id
            )
        ).first()
    return None


def handle_square_payment(db: Session, tenant_id: int, event: dict) -> dict:
    payment = ((event.get("data") or {}).get("object") or {}).get("payment") or {}
    if seen_before(db, "square", event.get("event_id") or "", event.get("type") or "", tenant_id):
        return {"ignored": "duplicate event"}
    order = _order_for_square(db, tenant_id, payment.get("order_id"), payment.get("id"))
    if order is None:
        return {"ignored": "not a snack box payment"}
    if payment.get("status") != "COMPLETED":
        return {"ignored": f"payment {str(payment.get('status')).lower()}"}
    paid = int((payment.get("amount_money") or {}).get("amount") or 0)
    if paid < order.amount_due_cents:
        order.attention = f"Square took {paid} cents but {order.amount_due_cents} were due. Check it before packing."
        db.flush()
        return {"ignored": "underpaid"}
    confirm_payment(db, order, "square", payment.get("id"))
    return {"box_order": order.id, "status": order.status.value}


def handle_square_refund(db: Session, tenant_id: int, event: dict) -> dict:
    refund_obj = ((event.get("data") or {}).get("object") or {}).get("refund") or {}
    if seen_before(db, "square", event.get("event_id") or "", event.get("type") or "", tenant_id):
        return {"ignored": "duplicate event"}
    order = _order_for_square(
        db, tenant_id, refund_obj.get("order_id"), refund_obj.get("payment_id")
    )
    if order is None:
        return {"ignored": "not a snack box refund"}
    if refund_obj.get("status") != "COMPLETED":
        return {"ignored": f"refund {str(refund_obj.get('status')).lower()}"}
    amount = int((refund_obj.get("amount_money") or {}).get("amount") or 0)
    if amount < order.amount_due_cents:
        order.attention = f"Square refunded {amount} of {order.amount_due_cents} cents. Decide what happens to the box."
        db.flush()
        return {"ignored": "partial refund"}
    packed = order.status in (BoxOrderStatus.PACKED, BoxOrderStatus.PICKED_UP)
    refund(db, order, restock=False)
    if packed and order.status is BoxOrderStatus.REFUNDED:
        order.attention = "Refunded in Square after packing. Restock it here only if the snacks can be sold again."
    db.flush()
    return {"box_order": order.id, "status": order.status.value}


# --- snack profiles ------------------------------------------------------------------------------


def snack_rows(db: Session, tenant_id: int) -> list[tuple[StockItem, SnackProfile | None]]:
    profiles = {
        p.stock_item_id: p
        for p in db.scalars(select(SnackProfile).where(SnackProfile.tenant_id == tenant_id))
    }
    items = db.scalars(
        select(StockItem)
        .where(
            StockItem.tenant_id == tenant_id,
            StockItem.is_active.is_(True),
            StockItem.dimension == Dimension.COUNT,
        )
        .order_by(StockItem.name)
    )
    return [(item, profiles.get(item.id)) for item in items]


def save_snack_profile(
    db: Session,
    tenant_id: int,
    stock_item_id: int,
    boxable: bool,
    flavors: set[str],
    origin: str,
    allergens: set[str] | None,
) -> list[str]:
    """allergens=None means the label has not been read; set() means it contains none of them."""
    item = db.get(StockItem, stock_item_id)
    if item is None or item.tenant_id != tenant_id:
        return ["No such item."]
    if set(flavors) - set(FLAVORS):
        return ["Unknown flavour."]
    if allergens is not None and set(allergens) - set(customers.ALLERGENS):
        return ["Unknown allergen."]
    if boxable and allergens is None:
        return [f"Record {item.name}'s allergens from the label before it can go in a box."]
    if boxable and item.dimension is not Dimension.COUNT:
        return ["Only items counted in units can go in a box."]
    profile = db.scalars(
        select(SnackProfile).where(SnackProfile.stock_item_id == stock_item_id)
    ).first()
    if profile is None:
        profile = SnackProfile(tenant_id=tenant_id, stock_item_id=stock_item_id)
        db.add(profile)
    profile.boxable = boxable
    profile.flavors = ";".join(sorted(flavors))
    profile.origin = origin.strip()[:40]
    profile.allergens = None if allergens is None else ";".join(sorted(allergens))
    profile.updated_at = now_utc()
    db.flush()
    return []
