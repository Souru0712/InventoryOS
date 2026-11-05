"""Public pages: the part that has to sell before anything else matters."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.web.templating import templates

router = APIRouter()

PLANS = [
    {
        "id": "starter",
        "name": "Starter",
        "price": 29,
        "blurb": "One location, everything you need to stop running out.",
        "features": [
            "Live inventory across count, weight and volume",
            "Receive by barcode scan or invoice photo",
            "Best-before dates scanned from the pack",
            "Markdown suggestions before stock expires",
            "Inventory health score and today's actions",
            "Email alerts, and counts pushed back to Square",
        ],
    },
    {
        "id": "pro",
        "name": "Pro",
        "price": 59,
        "blurb": "Multiple locations and the history to plan with.",
        "features": [
            "Everything in Starter",
            "Unlimited locations",
            "30-day health history, with changes explained",
            "Reorder list by supplier, as a CSV",
            "Custom alert thresholds and par levels",
        ],
    },
    {
        "id": "setup",
        "name": "Done-for-you setup",
        "price": 149,
        "one_time": True,
        "blurb": "We connect it, configure it and hand it over working.",
        "features": [
            "We connect your Square account",
            "We build your stock list with the right units",
            "We set reorder points from your real sales",
            "We configure alerts and your weekly report",
            "A 30 minute walkthrough with your team",
        ],
    },
]


@router.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse(
        request, "marketing/landing.html", {"plans": PLANS, "title": "StockPulse"}
    )


@router.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return templates.TemplateResponse(
        request, "marketing/pricing.html", {"plans": PLANS, "title": "Pricing"}
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
