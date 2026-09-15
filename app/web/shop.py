"""The shopper's side of snack boxes: email-link sign-in, preferences, ordering, history, survey, referrals."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BoxLineStatus, BoxOrderStatus, BoxSettings, Customer, ProductPreferenceState, Tenant
from app.orchestration import boxes, customers, referrals
from app.orchestration.views import tenant_currency
from app.security import current_customer, login_customer, logout_customer, require_customer
from app.transform.boxes import BOX_TYPES
from app.transform.taste import NOVELTY_LEVELS, RATINGS, SPICE_LEVELS
from app.web.flash import flash, pop_flash
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()

BLURBS = {
    "sweet": "Candy, chocolate, cookies and gummies.",
    "chaos": "Sweet, salty, spicy and something weird.",
    "world": "Snacks from as many countries as will fit.",
}
RATING_LABELS = {"loved": "Loved it", "liked": "Liked it", "neutral": "Meh", "disliked": "Didn't like it",
                 "never_again": "Never send again"}
SPICE_LABELS = {"none": "Nothing spicy", "mild": "Mild", "medium": "Medium", "hot": "Bring the heat"}
NOVELTY_LABELS = {"familiar": "Mostly favourites", "balanced": "Balanced", "surprise": "Surprise me"}
PRODUCT_STATES = {"favorite": ProductPreferenceState.FAVORITE, "never_again": ProductPreferenceState.NEVER_AGAIN,
                  "clear": None}


def _store(db: Session, tenant_id: int) -> Tenant:
    """The store, but only one that has set up snack boxes: other StockPulse workspaces have no shop."""
    tenant = db.get(Tenant, tenant_id)
    has_shop = db.scalars(select(BoxSettings.id).where(BoxSettings.tenant_id == tenant_id)).first()
    if tenant is None or has_shop is None:
        raise HTTPException(status_code=404, detail="no such store")
    return tenant


def _render(request: Request, db: Session, tenant: Tenant, customer: Customer | None, template: str,
            status_code: int = 200, **context):
    context.setdefault("title", tenant.name)
    return templates.TemplateResponse(
        request, template,
        {"shop": tenant, "customer": customer, "currency": tenant_currency(db, tenant.id),
         "flash": pop_flash(request), **context},
        status_code=status_code,
    )


def _box_types(settings) -> list[dict]:
    return [{"key": key, "blurb": BLURBS[key], "price_cents": getattr(settings, boxes.PRICE_FIELDS[key])}
            for key in BOX_TYPES]


def _own_order_or_404(db: Session, customer: Customer, order_id: int):
    order = customers.own_order(db, customer, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


# --- signing in ----------------------------------------------------------------------------------

@router.get("/shop/{tenant_id}", response_class=HTMLResponse)
def shop_home(request: Request, tenant_id: int, db: Session = Depends(get_db),
              customer: Customer | None = Depends(current_customer)):
    tenant = _store(db, tenant_id)
    settings = boxes.box_settings(db, tenant_id)
    return _render(request, db, tenant, customer, "shop/home.html", box=settings, box_types=_box_types(settings))


@router.get("/shop/{tenant_id}/login", response_class=HTMLResponse)
def login_form(request: Request, tenant_id: int, db: Session = Depends(get_db)):
    return _render(request, db, _store(db, tenant_id), None, "shop/login.html", title="Sign in")


@router.post("/shop/{tenant_id}/login")
def send_link(request: Request, tenant_id: int, email: str = Form(...), db: Session = Depends(get_db)):
    tenant = _store(db, tenant_id)
    email = customers.normalize_email(email)
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        return _render(request, db, tenant, None, "shop/login.html", 400, title="Sign in", email=email,
                       error="Enter the email address the link should go to.")
    known = db.scalars(select(Customer).where(Customer.tenant_id == tenant_id, Customer.email == email)).first()
    # A closed shop still signs its own customers in, but never opens accounts or mails strangers.
    if known is not None or boxes.box_settings(db, tenant_id).is_open:
        customers.issue_login_link(db, known or customers.find_or_create(db, tenant_id, email))
        db.commit()
    # The same page whether or not the address was known, so this form cannot be used to find accounts.
    return _render(request, db, tenant, None, "shop/check_email.html", title="Check your email", email=email)


@router.get("/shop/{tenant_id}/auth", response_class=HTMLResponse)
def confirm_link(request: Request, tenant_id: int, token: str = "", db: Session = Depends(get_db)):
    # Opening the link only shows a button. Mail scanners fetch links, and must not spend them.
    return _render(request, db, _store(db, tenant_id), None, "shop/auth.html", title="Sign in", token=token)


@router.post("/shop/{tenant_id}/auth")
def use_link(request: Request, tenant_id: int, token: str = Form(""), db: Session = Depends(get_db)):
    tenant = _store(db, tenant_id)
    customer = customers.redeem_login_token(db, tenant_id, token) if token else None
    if customer is None:
        return _render(request, db, tenant, None, "shop/login.html", 400, title="Sign in",
                       error="That sign-in link has expired or was already used. Ask for a new one.")
    db.commit()
    login_customer(request, customer)
    if customer.allergens_confirmed_at is None:
        return RedirectResponse(f"/shop/{tenant_id}/me/preferences?welcome=1", status_code=303)
    return RedirectResponse(f"/shop/{tenant_id}/me", status_code=303)


@router.get("/shop/{tenant_id}/logout")
def logout(request: Request, tenant_id: int):
    logout_customer(request)
    return RedirectResponse(f"/shop/{tenant_id}", status_code=303)


# --- account -----------------------------------------------------------------------------------

def _me(request: Request, db: Session, tenant: Tenant, customer: Customer, error: str | None = None,
        status_code: int = 200):
    settings = boxes.box_settings(db, tenant.id)
    return _render(request, db, tenant, customer, "shop/me.html", status_code, title="My box", box=settings,
                   box_types=_box_types(settings), orders=customers.history(db, customer)[:5],
                   summary=referrals.summary(db, customer), error=error)


@router.get("/shop/{tenant_id}/me", response_class=HTMLResponse)
def me(request: Request, tenant_id: int, db: Session = Depends(get_db),
       customer: Customer = Depends(require_customer)):
    response = _me(request, db, _store(db, tenant_id), customer)
    db.commit()
    return response


@router.post("/shop/{tenant_id}/me/orders")
def place_order(request: Request, tenant_id: int, box_type: str = Form(""), referral_code: str = Form(""),
                use_credit: str = Form(""), db: Session = Depends(get_db),
                customer: Customer = Depends(require_customer)):
    tenant = _store(db, tenant_id)
    try:
        order = boxes.place_order(db, tenant_id, customer, box_type, referral_code, use_credit == "on")
    except (boxes.BoxProblem, referrals.ReferralProblem) as exc:
        db.rollback()
        return _me(request, db, tenant, customer, error=str(exc), status_code=400)

    if order.status is BoxOrderStatus.PENDING_PAYMENT:
        try:
            link = boxes.square_checkout(db, order, customer)
        except Exception:
            log.exception("could not create a square checkout for box order %s", order.id)
            link = None
        if link:
            order.payment_url, order.square_order_id = link
            db.commit()
            return RedirectResponse(order.payment_url, status_code=303)
    db.commit()
    return RedirectResponse(f"/shop/{tenant_id}/me/orders/{order.id}?placed=1", status_code=303)


@router.get("/shop/{tenant_id}/me/orders", response_class=HTMLResponse)
def orders(request: Request, tenant_id: int, db: Session = Depends(get_db),
           customer: Customer = Depends(require_customer)):
    return _render(request, db, _store(db, tenant_id), customer, "shop/orders.html", title="Orders",
                   orders=customers.history(db, customer))


@router.get("/shop/{tenant_id}/me/orders/{order_id}", response_class=HTMLResponse)
def order_page(request: Request, tenant_id: int, order_id: int, db: Session = Depends(get_db),
               customer: Customer = Depends(require_customer)):
    order = _own_order_or_404(db, customer, order_id)
    # A mystery box stays a mystery until it has been collected.
    collected = order.picked_up_at is not None
    lines = customers.order_lines(db, order, [BoxLineStatus.PACKED, BoxLineStatus.RELEASED]) if collected else []
    return _render(request, db, _store(db, tenant_id), customer, "shop/order.html", title=f"Box #{order.id}",
                   order=order, lines=lines, reviews=customers.reviews_for(db, order))


@router.post("/shop/{tenant_id}/me/orders/{order_id}/cancel")
def cancel_order(request: Request, tenant_id: int, order_id: int, db: Session = Depends(get_db),
                 customer: Customer = Depends(require_customer)):
    order = _own_order_or_404(db, customer, order_id)
    problems = boxes.cancel(db, order)
    db.commit()
    flash(request, " ".join(problems) or "Order cancelled.", "error" if problems else "ok")
    return RedirectResponse(f"/shop/{tenant_id}/me/orders/{order.id}", status_code=303)


def _review_page(request, db, tenant_id, customer, order, error=None, status_code=200):
    return _render(request, db, _store(db, tenant_id), customer, "shop/review.html", status_code,
                   title="Rate your box", order=order, error=error, ratings=RATINGS, rating_labels=RATING_LABELS,
                   lines=customers.order_lines(db, order, [BoxLineStatus.PACKED]),
                   reviews=customers.reviews_for(db, order))


@router.get("/shop/{tenant_id}/me/orders/{order_id}/review", response_class=HTMLResponse)
def review_form(request: Request, tenant_id: int, order_id: int, db: Session = Depends(get_db),
                customer: Customer = Depends(require_customer)):
    return _review_page(request, db, tenant_id, customer, _own_order_or_404(db, customer, order_id))


@router.post("/shop/{tenant_id}/me/orders/{order_id}/review")
async def save_review(request: Request, tenant_id: int, order_id: int, db: Session = Depends(get_db),
                      customer: Customer = Depends(require_customer)):
    order = _own_order_or_404(db, customer, order_id)
    form = await request.form()
    answers = {
        line.stock_item_id: (str(form.get(f"rating_{line.stock_item_id}") or ""),
                             form.get(f"again_{line.stock_item_id}") == "on",
                             str(form.get(f"note_{line.stock_item_id}") or ""))
        for line, _ in customers.order_lines(db, order, [BoxLineStatus.PACKED])
    }
    overall = str(form.get("overall") or "")
    try:
        customers.submit_review(db, customer, order, answers, int(overall) if overall.isdigit() else None,
                                str(form.get("would_reorder") or "") or None)
    except customers.ReviewProblem as exc:
        db.rollback()
        return _review_page(request, db, tenant_id, customer, order, str(exc), 400)
    db.commit()
    flash(request, "Thanks - your next box will use this.")
    return RedirectResponse(f"/shop/{tenant_id}/me/orders/{order.id}", status_code=303)


# --- preferences ---------------------------------------------------------------------------------

def _categories(db: Session, tenant_id: int) -> list[str]:
    return sorted({item.category.strip() for item, profile in boxes.snack_rows(db, tenant_id)
                   if profile and profile.boxable and item.category and item.category.strip()})


def _preferences_page(request, db, tenant_id, customer, error=None, status_code=200, welcome=False):
    return _render(
        request, db, _store(db, tenant_id), customer, "shop/preferences.html", status_code, title="Preferences",
        error=error, welcome=welcome, allergens=customers.ALLERGENS, mine=customers.tags(customer.allergens),
        spice_levels=SPICE_LEVELS, spice_labels=SPICE_LABELS, novelty_levels=NOVELTY_LEVELS,
        novelty_labels=NOVELTY_LABELS, categories=_categories(db, tenant_id),
        liked=customers.tags(customer.liked_categories), avoided=customers.tags(customer.avoided_categories),
        product_prefs=customers.product_preferences(db, customer),
    )


@router.get("/shop/{tenant_id}/me/preferences", response_class=HTMLResponse)
def preferences(request: Request, tenant_id: int, welcome: int = 0, db: Session = Depends(get_db),
                customer: Customer = Depends(require_customer)):
    return _preferences_page(request, db, tenant_id, customer, welcome=bool(welcome))


@router.post("/shop/{tenant_id}/me/preferences")
async def save_preferences(request: Request, tenant_id: int, db: Session = Depends(get_db),
                           customer: Customer = Depends(require_customer)):
    form = await request.form()
    chosen = frozenset(a for a in customers.ALLERGENS if form.get(f"allergen_{a}") == "on")
    none = form.get("allergen_none") == "on"
    if not chosen and not none:
        return _preferences_page(request, db, tenant_id, customer, status_code=400,
                                 error="Tick each allergen that applies, or tick 'None of these'. We will not guess.")
    if chosen and none:
        return _preferences_page(request, db, tenant_id, customer, status_code=400,
                                 error="'None of these' cannot be ticked alongside an allergen.")
    known = {c.lower(): c for c in _categories(db, tenant_id)}
    leans = {key[len("lean:"):].lower(): str(value) for key, value in form.multi_items() if key.startswith("lean:")}
    try:
        customers.update_preferences(
            db, customer,
            allergens=chosen,
            spice=str(form.get("spice")) if form.get("spice") else None,
            novelty=str(form.get("novelty")) if form.get("novelty") else None,
            liked_categories=frozenset(c for c, v in leans.items() if v == "like" and c in known),
            avoided_categories=frozenset(c for c, v in leans.items() if v == "avoid" and c in known),
            dietary=str(form.get("dietary") or ""),
            first_name=str(form.get("first_name") or ""),
        )
    except ValueError as exc:
        db.rollback()
        return _preferences_page(request, db, tenant_id, customer, str(exc), 400)
    db.commit()
    flash(request, "Saved. Your next box will use these.")
    return RedirectResponse(f"/shop/{tenant_id}/me", status_code=303)


@router.post("/shop/{tenant_id}/me/products/{stock_item_id}")
def mark_product(request: Request, tenant_id: int, stock_item_id: int, state: str = Form(...),
                 next: str = Form(""), db: Session = Depends(get_db),
                 customer: Customer = Depends(require_customer)):
    if state not in PRODUCT_STATES:
        raise HTTPException(status_code=400, detail="unknown choice")
    try:
        customers.set_product_preference(db, customer, stock_item_id, PRODUCT_STATES[state])
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="item not found") from exc
    db.commit()
    flash(request, "Noted.")
    # Only ever back into this shop: a posted "next" must not become an open redirect.
    target = next if next.startswith(f"/shop/{tenant_id}/") else f"/shop/{tenant_id}/me/preferences"
    return RedirectResponse(target, status_code=303)


@router.get("/shop/{tenant_id}/me/referrals", response_class=HTMLResponse)
def referral_page(request: Request, tenant_id: int, db: Session = Depends(get_db),
                  customer: Customer = Depends(require_customer)):
    settings = boxes.box_settings(db, tenant_id)
    response = _render(request, db, _store(db, tenant_id), customer, "shop/referrals.html", title="Refer a friend",
                       box=settings, summary=referrals.summary(db, customer))
    db.commit()
    return response


@router.post("/shop/{tenant_id}/me/close")
def close_account(request: Request, tenant_id: int, db: Session = Depends(get_db),
                  customer: Customer = Depends(require_customer)):
    customers.deactivate(db, customer)
    db.commit()
    logout_customer(request)
    flash(request, "Your account is closed. Signing in again reopens it.")
    return RedirectResponse(f"/shop/{tenant_id}", status_code=303)
