"""Buying, changing and cancelling a subscription.

Prices and plans are decided here, never taken from the browser: a form can say "pro" but only
this process knows which Stripe price that is. Everything after payment arrives as a webhook and
is applied by app/orchestration/stripe_events.py.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.entitlements import PLAN_IDS
from app.models import Tenant, User
from app.orchestration.stripe_events import handle_event, price_for_plan
from app.security import require_tenant, require_user
from app.web.inputs import raw_body
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _stripe():
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="billing is not configured yet")
    import stripe

    stripe.api_key = settings.stripe_secret_key
    return stripe


@router.post("/billing/checkout")
def checkout(
    plan: str = Form(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    user: User = Depends(require_user),
):
    """Start a Stripe checkout for this account. Signing up comes first, so there is an account
    to attach the subscription to and nobody types their details twice."""
    settings = get_settings()
    if plan not in PLAN_IDS:
        raise HTTPException(status_code=400, detail=f"unknown plan {plan!r}")
    price_id = price_for_plan(plan)
    if not price_id:
        raise HTTPException(status_code=503, detail=f"no Stripe price configured for {plan}")

    stripe = _stripe()
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        customer=tenant.stripe_customer_id or None,
        customer_email=None if tenant.stripe_customer_id else user.email,
        client_reference_id=str(tenant.id),
        success_url=f"{settings.app_base_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.app_base_url}/pricing",
        metadata={"tenant_id": str(tenant.id), "plan": plan},
        subscription_data={"metadata": {"tenant_id": str(tenant.id), "plan": plan}},
    )
    log.info("tenant %s started checkout for %s", tenant.id, plan)
    return RedirectResponse(session.url, status_code=303)


@router.get("/billing/success", response_class=HTMLResponse)
def success(request: Request, tenant: Tenant = Depends(require_tenant)):
    """Stripe sends them back here. The webhook is what actually grants the plan, and it may
    arrive a second either side of this page, so the page does not promise anything it cannot see."""
    return templates.TemplateResponse(
        request, "app/billing_success.html", {"title": "You are subscribed", "tenant": tenant}
    )


@router.post("/billing/portal")
def portal(
    tenant: Tenant = Depends(require_tenant),
    _user: User = Depends(require_user),
):
    """Stripe's own portal: change card, switch plan, cancel. No email to us required."""
    settings = get_settings()
    if not tenant.stripe_customer_id:
        return RedirectResponse("/pricing", status_code=303)

    stripe = _stripe()
    session = stripe.billing_portal.Session.create(
        customer=tenant.stripe_customer_id,
        return_url=f"{settings.app_base_url}/app/settings",
    )
    return RedirectResponse(session.url, status_code=303)


@router.post("/billing/webhook")
def webhook(request: Request, payload: bytes = Depends(raw_body), db: Session = Depends(get_db)):
    """Stripe's word on what was paid. Verified, then applied once, whatever the order."""
    settings = get_settings()
    stripe = _stripe()
    signature = request.headers.get("stripe-signature", "")

    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="STRIPE_WEBHOOK_SECRET is not set")
    try:
        event = stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
    except Exception as exc:
        log.warning("rejected stripe webhook: %s", exc)
        raise HTTPException(status_code=400, detail="invalid signature") from exc

    result = handle_event(db, dict(event))
    db.commit()
    return {"received": True, **result}
