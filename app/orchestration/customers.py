"""Shopper accounts: magic-link sign-in, preferences with an audit trail, box history and the survey."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    BoxLine,
    BoxLineStatus,
    BoxOrder,
    BoxOrderStatus,
    BoxReview,
    Customer,
    CustomerLoginToken,
    CustomerProductPreference,
    PreferenceChange,
    ProductPreferenceState,
    SnackProfile,
    StockItem,
    Tenant,
)
from app.notify.email import render_pair, send_email
from app.orchestration.referrals import new_referral_code
from app.transform.taste import (
    NOVELTY_LEVELS,
    RATINGS,
    SPICE_LEVELS,
    Received,
    Review,
    Stated,
    Taste,
    build_taste,
)

log = logging.getLogger(__name__)
ALLERGENS = ("milk", "egg", "peanut", "tree_nut", "soy", "wheat", "fish", "shellfish", "sesame")
MAX_LINKS_PER_HOUR = 5
RECEIVED_ORDERS = (BoxOrderStatus.PACKED, BoxOrderStatus.PICKED_UP, BoxOrderStatus.REFUNDED)


class ReviewProblem(ValueError):
    """A survey answer that cannot be saved. The message is written for the customer."""


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def tags(text: str | None) -> frozenset[str]:
    return frozenset(t for t in (text or "").split(";") if t)


def join_tags(values) -> str:
    return ";".join(sorted({v.strip().lower() for v in values if v and v.strip()}))


def normalize_email(email: str) -> str:
    return email.strip().lower()


def find_or_create(
    db: Session, tenant_id: int, email: str, now: datetime | None = None
) -> Customer:
    email = normalize_email(email)
    customer = db.scalars(
        select(Customer).where(Customer.tenant_id == tenant_id, Customer.email == email)
    ).first()
    if customer is None:
        customer = Customer(
            tenant_id=tenant_id,
            email=email,
            referral_code=new_referral_code(db, tenant_id),
            created_at=now or datetime.now(UTC),
        )
        db.add(customer)
        db.flush()
    return customer


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_login_link(db: Session, customer: Customer, now: datetime | None = None) -> str | None:
    """Email a one-time sign-in link. None when this address has asked for too many links lately."""
    now = now or datetime.now(UTC)
    recent = db.scalar(
        select(func.count(CustomerLoginToken.id)).where(
            CustomerLoginToken.customer_id == customer.id,
            CustomerLoginToken.created_at >= now - timedelta(hours=1),
        )
    )
    if recent >= MAX_LINKS_PER_HOUR:
        log.warning("not sending customer %s another sign-in link this hour", customer.id)
        return None

    settings = get_settings()
    raw = secrets.token_urlsafe(32)
    minutes = settings.customer_login_ttl_minutes
    db.add(
        CustomerLoginToken(
            tenant_id=customer.tenant_id,
            customer_id=customer.id,
            token_hash=_hash(raw),
            expires_at=now + timedelta(minutes=minutes),
            created_at=now,
        )
    )
    db.flush()

    url = f"{settings.app_base_url.rstrip('/')}/shop/{customer.tenant_id}/auth?token={raw}"
    store = db.get(Tenant, customer.tenant_id).name
    html, text = render_pair("email/customer_login", url=url, store=store, minutes=minutes)
    sent = send_email(
        db,
        customer.tenant_id,
        customer.email,
        f"Your sign-in link for {store}",
        html,
        kind="customer_login",
        text=text,
    )
    if not sent and not settings.smtp_host:
        # Without a mail server the link is otherwise unusable; with one it never reaches a log.
        log.info("customer sign-in link for %s: %s", customer.email, url)
    return url


def redeem_login_token(
    db: Session, tenant_id: int, raw: str, now: datetime | None = None
) -> Customer | None:
    """The customer behind a valid, unused, unexpired link for this store; the link is spent."""
    now = now or datetime.now(UTC)
    token = db.scalars(
        select(CustomerLoginToken).where(
            CustomerLoginToken.tenant_id == tenant_id, CustomerLoginToken.token_hash == _hash(raw)
        )
    ).first()
    if token is None or token.used_at is not None or aware(token.expires_at) < now:
        return None
    customer = db.get(Customer, token.customer_id)
    if customer is None:
        return None
    token.used_at = now
    if not customer.is_active:
        # Signing back in is how a closed account reopens; its history and referral record were kept.
        customer.is_active = True
        customer.deactivated_at = None
        _audit(db, customer, "account", "closed", "open", "customer", now)
    db.flush()
    return customer


def _audit(
    db: Session, customer: Customer, field: str, old, new, source: str, now: datetime
) -> None:
    db.add(
        PreferenceChange(
            tenant_id=customer.tenant_id,
            customer_id=customer.id,
            field=field,
            old_value=None if old is None else str(old)[:500],
            new_value=None if new is None else str(new)[:500],
            source=source,
            changed_at=now,
        )
    )


def update_preferences(
    db: Session,
    customer: Customer,
    *,
    allergens: frozenset[str] | None = None,
    spice: str | None = None,
    novelty: str | None = None,
    liked_categories: frozenset[str] | None = None,
    avoided_categories: frozenset[str] | None = None,
    dietary: str | None = None,
    first_name: str | None = None,
    source: str = "customer",
    now: datetime | None = None,
) -> list[str]:
    """Save what the customer changed and audit each field. allergens=frozenset() means 'none'."""
    now = now or datetime.now(UTC)
    changed: list[str] = []

    def set_field(field: str, new) -> None:
        old = getattr(customer, field)
        if old != new:
            _audit(db, customer, field, old, new, source, now)
            setattr(customer, field, new)
            changed.append(field)

    if allergens is not None:
        unknown = set(allergens) - set(ALLERGENS)
        if unknown:
            raise ValueError(f"unknown allergens: {', '.join(sorted(unknown))}")
        set_field("allergens", join_tags(allergens))
        # Confirmed again even when unchanged: they looked at the answer and stood by it.
        customer.allergens_confirmed_at = now
    if spice is not None:
        if spice not in SPICE_LEVELS:
            raise ValueError("choose a spice level")
        set_field("spice", spice)
    if novelty is not None:
        if novelty not in NOVELTY_LEVELS:
            raise ValueError("choose how adventurous the box should be")
        set_field("novelty", novelty)
    liked = join_tags(liked_categories) if liked_categories is not None else None
    avoided = join_tags(avoided_categories) if avoided_categories is not None else None
    if liked is not None and avoided is not None and tags(liked) & tags(avoided):
        raise ValueError("a category cannot be both liked and avoided")
    if liked is not None:
        set_field("liked_categories", liked)
    if avoided is not None:
        set_field("avoided_categories", avoided)
    if dietary is not None:
        set_field("dietary", dietary.strip()[:200])
    if first_name is not None:
        set_field("first_name", first_name.strip()[:100] or None)
    db.flush()
    return changed


def set_product_preference(
    db: Session,
    customer: Customer,
    stock_item_id: int,
    state: ProductPreferenceState | None,
    source: str = "customer",
    now: datetime | None = None,
) -> None:
    """Favourite, never-again, or clear (None). Every change is audited, including an undo."""
    now = now or datetime.now(UTC)
    item = db.get(StockItem, stock_item_id)
    if item is None or item.tenant_id != customer.tenant_id:
        raise LookupError("no such item at this store")
    row = db.scalars(
        select(CustomerProductPreference).where(
            CustomerProductPreference.customer_id == customer.id,
            CustomerProductPreference.stock_item_id == stock_item_id,
        )
    ).first()
    old = row.state.value if row else None
    new = state.value if state else None
    if old == new:
        return
    if state is None:
        db.delete(row)
    elif row is None:
        db.add(
            CustomerProductPreference(
                tenant_id=customer.tenant_id,
                customer_id=customer.id,
                stock_item_id=stock_item_id,
                state=state,
                source=source,
                created_at=now,
            )
        )
    else:
        row.state, row.source = state, source
    _audit(db, customer, f"product:{stock_item_id}", old, new, source, now)
    db.flush()


def product_preferences(
    db: Session, customer: Customer
) -> list[tuple[CustomerProductPreference, StockItem]]:
    return list(
        db.execute(
            select(CustomerProductPreference, StockItem)
            .join(StockItem, StockItem.id == CustomerProductPreference.stock_item_id)
            .where(CustomerProductPreference.customer_id == customer.id)
            .order_by(StockItem.name)
        ).all()
    )


def own_order(db: Session, customer: Customer, order_id: int) -> BoxOrder | None:
    """An order only if it belongs to this customer; anything else reads as not found."""
    order = db.get(BoxOrder, order_id)
    if order is None or order.customer_id != customer.id or order.tenant_id != customer.tenant_id:
        return None
    return order


def order_lines(db: Session, order: BoxOrder, statuses=None) -> list[tuple[BoxLine, StockItem]]:
    query = (
        select(BoxLine, StockItem)
        .join(StockItem, StockItem.id == BoxLine.stock_item_id)
        .where(BoxLine.box_order_id == order.id)
    )
    if statuses is not None:
        query = query.where(BoxLine.status.in_(statuses))
    return list(db.execute(query.order_by(StockItem.name)).all())


def history(db: Session, customer: Customer) -> list[BoxOrder]:
    return list(
        db.scalars(
            select(BoxOrder)
            .where(BoxOrder.customer_id == customer.id)
            .order_by(BoxOrder.created_at.desc())
        )
    )


def reviews_for(db: Session, order: BoxOrder) -> dict[int, BoxReview]:
    return {
        r.stock_item_id: r
        for r in db.scalars(select(BoxReview).where(BoxReview.box_order_id == order.id))
    }


def submit_review(
    db: Session,
    customer: Customer,
    order: BoxOrder,
    answers: dict[int, tuple[str, bool, str]],
    overall: int | None = None,
    would_reorder: str | None = None,
    now: datetime | None = None,
) -> int:
    """Save survey answers for a picked-up box. Every question is optional; answering again corrects."""
    now = now or datetime.now(UTC)
    if order.customer_id != customer.id:
        raise LookupError("not this customer's box")
    if order.status is not BoxOrderStatus.PICKED_UP:
        raise ReviewProblem("You can rate a box once you have picked it up.")
    in_box = {line.stock_item_id for line, _ in order_lines(db, order, [BoxLineStatus.PACKED])}
    existing = reviews_for(db, order)

    saved = 0
    for item_id, (rating, send_again, note) in answers.items():
        if item_id not in in_box:
            raise ReviewProblem("That item was not in this box.")
        if not rating:
            continue
        if rating not in RATINGS:
            raise ReviewProblem("Pick one of the answers for each snack.")
        send_again = (
            bool(send_again) and rating != "never_again"
        )  # never again wins a contradiction
        row = existing.get(item_id)
        was_never = row is not None and row.rating == "never_again"
        if row is None:
            row = BoxReview(
                tenant_id=customer.tenant_id,
                customer_id=customer.id,
                box_order_id=order.id,
                stock_item_id=item_id,
            )
            db.add(row)
        row.rating, row.send_again = rating, send_again
        row.note = (note or "").strip()[:500] or None
        row.submitted_at = now
        if rating == "never_again":
            set_product_preference(
                db, customer, item_id, ProductPreferenceState.NEVER_AGAIN, "survey", now
            )
        elif was_never:
            _clear_survey_never_again(db, customer, item_id, now)
        saved += 1

    if overall is not None:
        if not 1 <= overall <= 5:
            raise ReviewProblem("Rate the box from 1 to 5.")
        order.overall_rating = overall
    if would_reorder in ("yes", "maybe", "no"):
        order.would_reorder = would_reorder
    db.flush()
    return saved


def _clear_survey_never_again(db: Session, customer: Customer, item_id: int, now: datetime) -> None:
    """They changed a survey answer away from never-again; only a survey-made entry follows it."""
    row = db.scalars(
        select(CustomerProductPreference).where(
            CustomerProductPreference.customer_id == customer.id,
            CustomerProductPreference.stock_item_id == item_id,
        )
    ).first()
    if (
        row is not None
        and row.state is ProductPreferenceState.NEVER_AGAIN
        and row.source == "survey"
    ):
        set_product_preference(db, customer, item_id, None, "survey", now)


def deactivate(db: Session, customer: Customer, now: datetime | None = None) -> None:
    """Close the account. Orders, reviews, referrals and credit stay: they are financial history."""
    now = now or datetime.now(UTC)
    if not customer.is_active:
        return
    customer.is_active = False
    customer.deactivated_at = now
    for token in db.scalars(
        select(CustomerLoginToken).where(
            CustomerLoginToken.customer_id == customer.id, CustomerLoginToken.used_at.is_(None)
        )
    ):
        token.used_at = now
    _audit(db, customer, "account", "open", "closed", "customer", now)
    db.flush()


def taste_for(db: Session, customer: Customer) -> Taste:
    prefs = product_preferences(db, customer)
    stated = Stated(
        allergens=tags(customer.allergens),
        spice=customer.spice,
        novelty=customer.novelty,
        liked_categories=tags(customer.liked_categories),
        avoided_categories=tags(customer.avoided_categories),
        favorites=frozenset(
            p.stock_item_id for p, _ in prefs if p.state is ProductPreferenceState.FAVORITE
        ),
        never_again=frozenset(
            p.stock_item_id for p, _ in prefs if p.state is ProductPreferenceState.NEVER_AGAIN
        ),
    )
    reviews = [
        Review(r.stock_item_id, r.rating, r.send_again, aware(r.submitted_at))
        for r in db.scalars(select(BoxReview).where(BoxReview.customer_id == customer.id))
    ]
    received = [
        Received(order.id, line.stock_item_id, aware(order.packed_at))
        for line, order in db.execute(
            select(BoxLine, BoxOrder)
            .join(BoxOrder, BoxOrder.id == BoxLine.box_order_id)
            .where(
                BoxOrder.customer_id == customer.id,
                BoxOrder.status.in_(RECEIVED_ORDERS),
                BoxOrder.packed_at.is_not(None),
                BoxLine.status == BoxLineStatus.PACKED,
            )
        ).all()
    ]
    item_ids = (
        {r.stock_item_id for r in received} | {r.stock_item_id for r in reviews} | stated.favorites
    )
    categories, origins = {}, {}
    if item_ids:
        for item in db.scalars(select(StockItem).where(StockItem.id.in_(item_ids))):
            categories[item.id] = item.category or ""
        for profile in db.scalars(
            select(SnackProfile).where(SnackProfile.stock_item_id.in_(item_ids))
        ):
            origins[profile.stock_item_id] = profile.origin
    return build_taste(stated, reviews, received, categories, origins)
