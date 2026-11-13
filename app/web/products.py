"""Product and modifier screens (T2.01; RCP-FR-01, -03, C-36): /app/products.

Anyone who can read recipes sees what the business sells; changing it takes product.edit and the
subscription's write rule. Prices are money, so only a role with cost access sees them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext
from app.db import get_db
from app.models import Tenant
from app.orchestration.views import tenant_currency
from app.products import (
    NewVariation,
    NotFound,
    add_variation,
    categories,
    create_product,
    get_product,
    list_modifiers,
    list_products,
    save_modifier,
    set_product_active,
    update_product,
    update_variation,
)
from app.security import require_permission, require_tenant, require_writable
from app.throttle import client_ip
from app.transform.products import EFFECT_LABELS, MODIFIER_EFFECTS, ProductRuleError
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()

BASE = "/app/products"
NEW_ROWS = 4  # size rows on the new-product form; more are added on the product's page


def _act(request: Request, db: Session, action, done: str, back: str) -> RedirectResponse:
    try:
        action()
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except ProductRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(back, status_code=303)
    db.commit()
    flash(request, done)
    return RedirectResponse(back, status_code=303)


@router.get(BASE, response_class=HTMLResponse)
def products_page(
    request: Request,
    show: str = "active",
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("recipe.view")),
):
    return templates.TemplateResponse(
        request,
        "app/products.html",
        {
            "title": "Products",
            "tenant": tenant,
            "message": pop_flash(request),
            "products": list_products(db, org, inactive=show == "all"),
            "show_all": show == "all",
            "modifiers": list_modifiers(db, org),
            "effects": MODIFIER_EFFECTS,
            "effect_labels": EFFECT_LABELS,
            "currency": tenant_currency(db, org),
        },
    )


def _form(request: Request, db: Session, org: OrgContext, tenant: Tenant, **values):
    return templates.TemplateResponse(
        request,
        "app/product_form.html",
        {
            "title": "New product",
            "tenant": tenant,
            "categories": categories(db, org),
            "rows": NEW_ROWS,
            "values": values,
            "currency": tenant_currency(db, org),
        },
    )


@router.get(BASE + "/new", response_class=HTMLResponse)
def new_product_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("product.edit")),
):
    return _form(request, db, org, tenant)


@router.post(BASE + "/new")
def add_product(
    request: Request,
    name: str = Form(""),
    category: str = Form(""),
    variation_name: list[str] = Form([]),
    variation_price: list[str] = Form([]),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("product.edit")),
):
    prices = variation_price + [""] * (len(variation_name) - len(variation_price))
    sizes = [NewVariation(n, p) for n, p in zip(variation_name, prices, strict=False)]
    try:
        product = create_product(
            db, org, name=name, category=category, variations=sizes, ip=client_ip(request)
        )
    except ProductRuleError as exc:
        db.rollback()
        response = _form(
            request, db, org, tenant, name=name, category=category, sizes=sizes, error=str(exc)
        )
        response.status_code = 400
        return response
    db.commit()
    flash(request, f"{product.name} added.")
    return RedirectResponse(f"{BASE}/{product.id}", status_code=303)


@router.get(BASE + "/{product_id}", response_class=HTMLResponse)
def product_page(
    request: Request,
    product_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("recipe.view")),
):
    try:
        product = get_product(db, org, product_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="product not found") from exc
    return templates.TemplateResponse(
        request,
        "app/product.html",
        {
            "title": product.name,
            "tenant": tenant,
            "message": pop_flash(request),
            "product": product,
            "categories": categories(db, org),
            "currency": tenant_currency(db, org),
        },
    )


@router.post(BASE + "/{product_id}")
def edit_product(
    request: Request,
    product_id: int,
    name: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("product.edit")),
):
    ip = client_ip(request)
    return _act(
        request,
        db,
        lambda: update_product(db, org, product_id, name=name, category=category, ip=ip),
        "Saved.",
        f"{BASE}/{product_id}",
    )


@router.post(BASE + "/{product_id}/active")
def product_active(
    request: Request,
    product_id: int,
    active: str = Form("no"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("product.edit")),
):
    on = active == "yes"
    ip = client_ip(request)
    return _act(
        request,
        db,
        lambda: set_product_active(db, org, product_id, on, ip=ip),
        "Back on sale." if on else "Deactivated. Its history stays.",
        f"{BASE}/{product_id}",
    )


@router.post(BASE + "/{product_id}/variations")
def new_variation(
    request: Request,
    product_id: int,
    name: str = Form(""),
    price: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("product.edit")),
):
    ip = client_ip(request)
    return _act(
        request,
        db,
        lambda: add_variation(db, org, product_id, NewVariation(name, price), ip=ip),
        "Size added.",
        f"{BASE}/{product_id}",
    )


@router.post(BASE + "/{product_id}/variations/{variation_id}")
def edit_variation(
    request: Request,
    product_id: int,
    variation_id: int,
    name: str = Form(""),
    price: str | None = Form(None),  # absent for a role that may not see prices: unchanged
    active: str = Form("no"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("product.edit")),
):
    ip = client_ip(request)
    return _act(
        request,
        db,
        lambda: update_variation(
            db,
            org,
            product_id,
            variation_id,
            name=name,
            price=price,
            active=active == "yes",
            ip=ip,
        ),
        "Saved.",
        f"{BASE}/{product_id}",
    )


@router.post("/app/modifiers")
def new_modifier(
    request: Request,
    name: str = Form(""),
    group: str = Form(""),
    effect: str = Form("none"),
    scale_factor: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("product.edit")),
):
    ip = client_ip(request)
    return _act(
        request,
        db,
        lambda: save_modifier(
            db, org, name=name, group=group, effect=effect, scale_factor=scale_factor, ip=ip
        ),
        "Modifier added.",
        f"{BASE}#modifiers",
    )


@router.post("/app/modifiers/{modifier_id}")
def edit_modifier(
    request: Request,
    modifier_id: int,
    name: str = Form(""),
    group: str = Form(""),
    effect: str = Form("none"),
    scale_factor: str = Form(""),
    active: str = Form("no"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("product.edit")),
):
    ip = client_ip(request)
    return _act(
        request,
        db,
        lambda: save_modifier(
            db,
            org,
            name=name,
            group=group,
            effect=effect,
            scale_factor=scale_factor,
            active=active == "yes",
            modifier_id=modifier_id,
            ip=ip,
        ),
        "Saved.",
        f"{BASE}#modifiers",
    )
