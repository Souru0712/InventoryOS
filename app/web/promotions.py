"""Markdowns: what the numbers suggest, and what the owner decides to do about it."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped
from app.db import get_db
from app.models import Promotion, StockItem, Tenant, User
from app.orchestration.promotions import (
    accept,
    dismiss,
    end,
    find_suggestion,
    running,
    snooze,
    suggestions_for,
)
from app.orchestration.views import tenant_currency
from app.security import require_org, require_tenant, require_user, require_writable
from app.units import to_base
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/app/promotions", response_class=HTMLResponse)
def promotions_page(
    request: Request,
    location: int | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_org),
):
    today = datetime.now(UTC).date()
    suggestions = suggestions_for(db, org, today, location)
    return templates.TemplateResponse(
        request,
        "app/promotions.html",
        {
            "title": "Promotions",
            "tenant": tenant,
            "suggestions": suggestions,
            "running": running(db, org, today),
            "currency": tenant_currency(db, org),
            "at_risk_cents": sum(s.cost_at_risk_cents for s in suggestions),
        },
    )


def _suggestion_or_404(db: Session, org: OrgContext, stock_item_id: int):
    suggestion = find_suggestion(suggestions_for(db, org), stock_item_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="that suggestion no longer applies")
    return suggestion


@router.post("/app/promotions/accept")
def accept_promotion(
    stock_item_id: int = Form(...),
    price: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_org),
):
    """Start the markdown. An empty price means the suggested one was good enough."""
    item = get_scoped(db, org, StockItem, stock_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="stock item not found")
    suggestion = _suggestion_or_404(db, org, stock_item_id)

    price_cents = suggestion.suggested_price_cents
    if price.strip():
        try:
            # Typed in the money the shelf label shows, then stored per base unit.
            entered = (Decimal(price.strip()) * 100) / to_base(1, item.stock_unit)
        except (InvalidOperation, ArithmeticError) as exc:
            raise HTTPException(status_code=400, detail="that is not a price") from exc
        if entered <= 0:
            raise HTTPException(status_code=400, detail="a price has to be above zero")
        price_cents = int(entered.quantize(Decimal("1")))

    accept(db, org, suggestion, price_cents, user_id=user.id)
    db.commit()
    return RedirectResponse("/app/promotions?started=1", status_code=303)


@router.post("/app/promotions/dismiss")
def dismiss_promotion(
    stock_item_id: int = Form(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_org),
):
    dismiss(db, org, _suggestion_or_404(db, org, stock_item_id), user_id=user.id)
    db.commit()
    return RedirectResponse("/app/promotions?dismissed=1", status_code=303)


@router.post("/app/promotions/snooze")
def snooze_promotion(
    stock_item_id: int = Form(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_org),
):
    snooze(db, org, _suggestion_or_404(db, org, stock_item_id), user_id=user.id)
    db.commit()
    return RedirectResponse("/app/promotions?snoozed=1", status_code=303)


@router.post("/app/promotions/{promotion_id}/end")
def end_promotion(
    promotion_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_org),
):
    promotion = get_scoped(db, org, Promotion, promotion_id)
    if promotion is None:
        raise HTTPException(status_code=404, detail="promotion not found")
    end(db, org, promotion)
    db.commit()
    return RedirectResponse("/app/promotions?ended=1", status_code=303)
