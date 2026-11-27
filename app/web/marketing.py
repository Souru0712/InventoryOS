"""Public pages: the part that has to sell before anything else matters.

The plans, their names, how each is billed and its price come from the plan catalogue (T4.02,
ADR-0090), never from here: a plan's price is the provider's, copied when it is set (E-01). What
this module adds is copy, chosen by what a plan's entitlements allow, so a new plan in the
catalogue shows without a code change (E-03).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BillingPlan
from app.plans import public_plans
from app.web.templating import money, templates

router = APIRouter()

# The complete core, on every plan (PRICING_AND_PACKAGING §4, E-05).
CORE = (
    "Stock in counts, weights and volumes, per location",
    "Recipes and their costs, with every version kept",
    "Deliveries received, invoices read from a photo",
    "Counts, waste, batches and expiry dates",
    "Reorder suggestions and the core reports",
    "Staff roles, an audit trail, and your data exported any time",
)
# What each sales mode adds (PRICING_AND_PACKAGING §3).
BY_MODE = {
    "connected": (
        "Square sales take their ingredients out of stock on their own",
        "Refunds and voids handled; sales checked against Square daily",
        "Connection health on the dashboard",
    ),
    "standalone": (
        "Daily sales entered by hand or imported from a file",
        "Missing days flagged; usage worked out between counts",
    ),
}
INTERVALS = {"month": "month", "year": "year", "week": "week", "day": "day"}


@dataclass(frozen=True)
class PlanCard:
    code: str
    name: str
    price: str | None  # None until the plan names a provider price
    per: str
    reach: str
    features: tuple[str, ...]


def card(plan: BillingPlan) -> PlanCard:
    granted = plan.entitlement
    modes = set(granted.allowed_modes or ())
    shown = "connected" if "connected" in modes else "standalone"
    interval = INTERVALS.get(plan.display_interval or "month", plan.display_interval or "month")
    per_location = plan.billing_basis == "per_location"
    limit = granted.max_locations
    return PlanCard(
        code=plan.code,
        name=plan.name,
        price=(
            money(plan.display_amount_cents, plan.display_currency or "USD")
            if plan.display_amount_cents is not None
            else None
        ),
        per=f"per location a {interval}" if per_location else f"a {interval}",
        reach=(
            "As many locations as you connect"
            if limit is None
            else f"{limit} location{'' if limit == 1 else 's'}"
        ),
        features=BY_MODE[shown],
    )


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "marketing/landing.html",
        {"plans": [card(plan) for plan in public_plans(db)], "core": CORE, "title": "Vontry"},
    )


@router.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "marketing/pricing.html",
        {"plans": [card(plan) for plan in public_plans(db)], "core": CORE, "title": "Pricing"},
    )


@router.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    return templates.TemplateResponse(
        request, "marketing/terms.html", {"title": "Terms of Service"}
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(
        request, "marketing/privacy.html", {"title": "Privacy Policy"}
    )
