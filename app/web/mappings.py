"""The POS mapping screens (T3.04; POS-FR-06, POS-FR-14, WF-28): /app/mappings.

Each POS product maps to one of the business's sizes (suggested by name, or made from it), each
POS modifier to one of its modifiers. A product that sold while unmapped goes on to a preview of
reprocessing those sales, which runs only once confirmed.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext
from app.connections import current, name_of
from app.db import get_db
from app.mappings import (
    MappingRuleError,
    NotFound,
    NotMapped,
    create_and_map,
    map_modifier,
    map_product,
    modifier_rows,
    preview,
    product_rows,
    queue_reprocess,
    sizes,
    unmapped_sales,
)
from app.models import Tenant
from app.products import ProductRuleError, list_modifiers
from app.security import require_permission, require_tenant, require_writable
from app.throttle import client_ip
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()
PAGE = "/app/mappings"
PROVIDER = "square"
NEW = "new"


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


def _id(raw: str) -> int | None:
    try:
        return int(raw) if raw.strip() else None
    except ValueError as exc:
        raise _not_found(exc) from exc


@router.get(PAGE, response_class=HTMLResponse)
def mappings_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("mapping.manage")),
):
    connection = current(db, org, PROVIDER)
    choices = sizes(db, org)
    return templates.TemplateResponse(
        request,
        "app/mappings.html",
        {
            "title": "Product mapping",
            "tenant": tenant,
            "message": pop_flash(request),
            "provider": name_of(PROVIDER),
            "connection": connection,
            "rows": product_rows(db, org, connection) if connection else [],
            "modifiers": modifier_rows(db, org, connection) if connection else [],
            "sizes": choices,
            "sizes_by_id": {v.id: v for v in choices},
            "own_modifiers": list_modifiers(db, org),
        },
    )


@router.post(PAGE + "/products/{external_product_id}")
def map_pos_product(
    request: Request,
    external_product_id: int,
    target: str = Form(""),
    db: Session = Depends(get_db),
    _writable: Tenant = Depends(require_writable),  # read-only refuses it (AC-SAAS-07)
    org: OrgContext = Depends(require_permission("mapping.manage")),
):
    """Map to a size, make the product from the POS one ("new"), or unmap ("")."""
    ip = client_ip(request)
    try:
        if target == NEW:
            create_and_map(db, org, external_product_id, ip=ip)
            changed = True
        else:
            changed = map_product(db, org, external_product_id, _id(target), ip=ip)
    except NotFound as exc:
        raise _not_found(exc) from exc
    except (MappingRuleError, ProductRuleError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(PAGE, status_code=303)
    db.commit()
    if changed and target and unmapped_sales(db, org, external_product_id):
        # It sold while unmapped: offer to reprocess those sales (WF-28 step 3).
        return RedirectResponse(f"{PAGE}/products/{external_product_id}/reprocess", status_code=303)
    flash(request, "Saved." if changed else "Nothing changed.")
    return RedirectResponse(PAGE, status_code=303)


@router.post(PAGE + "/modifiers/{external_modifier_id}")
def map_pos_modifier(
    request: Request,
    external_modifier_id: int,
    modifier_id: str = Form(""),
    db: Session = Depends(get_db),
    _writable: Tenant = Depends(require_writable),  # read-only refuses it (AC-SAAS-07)
    org: OrgContext = Depends(require_permission("mapping.manage")),
):
    try:
        changed = map_modifier(
            db, org, external_modifier_id, _id(modifier_id), ip=client_ip(request)
        )
    except NotFound as exc:
        raise _not_found(exc) from exc
    db.commit()
    flash(request, "Saved." if changed else "Nothing changed.")
    return RedirectResponse(PAGE, status_code=303)


def _start(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


@router.get(PAGE + "/products/{external_product_id}/reprocess", response_class=HTMLResponse)
def reprocess_page(
    request: Request,
    external_product_id: int,
    start: str | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("mapping.manage")),
):
    error = None
    try:
        shown = preview(db, org, external_product_id, _start(start))
    except NotFound as exc:
        raise _not_found(exc) from exc
    except NotMapped as exc:
        flash(request, str(exc), "error")
        return RedirectResponse(PAGE, status_code=303)
    except MappingRuleError as exc:  # a start out of range: show the default instead
        error = str(exc)
        shown = preview(db, org, external_product_id)
    return templates.TemplateResponse(
        request,
        "app/mapping_reprocess.html",
        {"title": "Reprocess sales", "tenant": tenant, "preview": shown, "error": error},
    )


@router.post(PAGE + "/products/{external_product_id}/reprocess")
def reprocess_confirm(
    request: Request,
    external_product_id: int,
    start: str = Form(""),
    db: Session = Depends(get_db),
    _writable: Tenant = Depends(require_writable),  # read-only refuses it (AC-SAAS-07)
    org: OrgContext = Depends(require_permission("mapping.manage")),
):
    chosen = _start(start)
    try:
        if chosen is None:
            raise MappingRuleError("Choose the date reprocessing starts from.")
        shown = queue_reprocess(db, org, external_product_id, chosen, ip=client_ip(request))
    except NotFound as exc:
        raise _not_found(exc) from exc
    except MappingRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(f"{PAGE}/products/{external_product_id}/reprocess", status_code=303)
    db.commit()
    flash(
        request,
        f"Reprocessing {shown.sales} sale(s) in the background."
        if shown.sales
        else "There were no sales to reprocess.",
    )
    return RedirectResponse(PAGE, status_code=303)
