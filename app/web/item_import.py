"""Item import and quick-add screens (T2.20-a; INV-FR-08, AC-INV-12): /app/items/import and
/app/items/quick-add.

Upload a CSV, see every row with what would stop it, import the rows that pass; or type up to
twelve items into a grid.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.access import OrgContext, select_scoped
from app.db import get_db
from app.item_import import (
    NotFound,
    RowResult,
    import_items,
    preview_items,
    quick_add,
    upload_items,
)
from app.models import Location, Tenant
from app.security import location_filter, require_permission, require_tenant, require_writable
from app.transform.item_import import TEMPLATE_HEADER, ItemImportError, item_rows
from app.units import UNITS
from app.web.flash import flash, pop_flash
from app.web.inputs import posted_form
from app.web.templating import templates

router = APIRouter()
BASE = "/app/items/import"
QUICK = "/app/items/quick-add"
GRID_ROWS = 12


def _places(db: Session, org: OrgContext) -> list[Location]:
    return list(db.scalars(select_scoped(org, Location).order_by(Location.id)))


def _results(request, tenant, title, results: list[RowResult], **extra) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "app/item_import_rows.html",
        {
            "title": title,
            "tenant": tenant,
            "results": results,
            "ready": sum(1 for r in results if r.error is None),
            "refused": sum(1 for r in results if r.error is not None),
            **extra,
        },
    )


@router.get(BASE, response_class=HTMLResponse)
def import_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    return templates.TemplateResponse(
        request,
        "app/item_import.html",
        {
            "title": "Add items",
            "tenant": tenant,
            "message": pop_flash(request),
            "places": _places(db, org),
            "template_header": TEMPLATE_HEADER,
        },
    )


@router.get(BASE + "/template.csv")
def template_csv(
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    rows = [
        ",".join(TEMPLATE_HEADER),
        "Whole milk,l,Dairy,12,6,20,1.10,raw",
        "Coffee cups 12oz,each,Packaging,500,200,1000,0.08,packaging",
    ]
    return Response(
        "\r\n".join(rows) + "\r\n",
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="items-template.csv"'},
    )


@router.post(BASE)
def upload(
    request: Request,
    location_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    if not any(p.id == location_id for p in _places(db, org)):
        raise HTTPException(status_code=404, detail="not found")
    try:
        digest = upload_items(db, org, file.file.read())
    except ItemImportError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse(BASE, status_code=303)
    return RedirectResponse(f"{BASE}/{digest}?location={location_id}", status_code=303)


@router.get(BASE + "/{digest}", response_class=HTMLResponse)
def preview(
    request: Request,
    digest: str,
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    places = _places(db, org)
    if location is None and places:
        location = places[0].id
    try:
        results = preview_items(db, org, digest, location or 0)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    finally:
        db.rollback()  # the preview kept nothing; this makes sure of it
    return _results(
        request,
        tenant,
        "Check your items",
        results,
        digest=digest,
        location=location,
        place=next((p for p in places if p.id == location), None),
        imported=False,
    )


@router.post(BASE + "/{digest}")
def run_import(
    request: Request,
    digest: str,
    location_id: int = Form(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    try:
        results = import_items(db, org, digest, location_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    db.commit()
    return _results(request, tenant, "Items imported", results, imported=True)


@router.get(QUICK, response_class=HTMLResponse)
def quick_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    return templates.TemplateResponse(
        request,
        "app/item_quick_add.html",
        {
            "title": "Quick add",
            "tenant": tenant,
            "places": _places(db, org),
            "rows": range(1, GRID_ROWS + 1),
            "units": sorted(UNITS),
        },
    )


@router.post(QUICK)
def quick_save(
    request: Request,
    location_id: int = Form(...),
    form: FormData = Depends(posted_form),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    header = ["name", "unit", "quantity", "reorder point", "cost", "category"]
    grid = [
        [
            str(form.get(f"{field}_{n}") or "")
            for field in ("name", "unit", "qty", "reorder", "cost", "category")
        ]
        for n in range(1, GRID_ROWS + 1)
    ]
    try:
        results = quick_add(db, org, item_rows(header, grid), location_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    db.commit()
    if not results:
        flash(request, "Type at least one item name.", "error")
        return RedirectResponse(QUICK, status_code=303)
    return _results(request, tenant, "Items added", results, imported=True)
