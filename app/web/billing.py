"""Stripe checkout and webhook. Subscription state lives on the tenant."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Plan, Tenant
from app.security import require_tenant, require_user
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()

PRICE_FOR_PLAN = {"starter": "stripe_price_starter", "pro": "stripe_price_pro"}


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
    user=Depends(require_user),
):
    settings = get_settings()
    price_attr = PRICE_FOR_PLAN.get(plan)
    if price_attr is None:
        raise HTTPException(status_code=400, detail=f"unknown plan {plan!r}")
    price_id = getattr(settings, price_attr)
    if not price_id:
        raise HTTPException(status_code=503, detail=f"no Stripe price configured for {plan}")

    stripe = _stripe()
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        customer_email=user.email,
        client_reference_id=str(tenant.id),
        success_url=f"{settings.app_base_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.app_base_url}/pricing",
        metadata={"tenant_id": str(tenant.id), "plan": plan},
    )
    return RedirectResponse(session.url, status_code=303)


@router.get("/billing/success", response_class=HTMLResponse)
def success(request: Request, tenant: Tenant = Depends(require_tenant)):
    return templates.TemplateResponse(
        request, "app/billing_success.html", {"title": "You are subscribed", "tenant": tenant}
    )


@router.post("/billing/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    stripe = _stripe()
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")

    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="STRIPE_WEBHOOK_SECRET is not set")
    try:
        event = stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
    except Exception as exc:
        log.warning("rejected stripe webhook: %s", exc)
        raise HTTPException(status_code=400, detail="invalid signature") from exc

    obj = event["data"]["object"]
    kind = event["type"]

    if kind == "checkout.session.completed":
        tenant_id = int(obj.get("metadata", {}).get("tenant_id") or obj.get("client_reference_id") or 0)
        plan = obj.get("metadata", {}).get("plan", "starter")
        tenant = db.get(Tenant, tenant_id)
        if tenant:
            tenant.plan = Plan(plan)
            tenant.stripe_customer_id = obj.get("customer")
            db.commit()
            log.info("tenant %s subscribed to %s", tenant_id, plan)
    elif kind in {"customer.subscription.deleted", "customer.subscription.paused"}:
        customer_id = obj.get("customer")
        tenant = db.scalars(select(Tenant).where(Tenant.stripe_customer_id == customer_id)).first()
        if tenant:
            tenant.plan = Plan.CANCELED
            db.commit()
            log.info("tenant %s subscription canceled", tenant.id)

    return {"received": True}
