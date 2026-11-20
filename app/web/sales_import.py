"""Sales file import (T2.13; STD-FR-03, AC-SAL-04): /app/sales/import.

Upload a CSV, choose its columns once, match the names it uses, check the preview, post.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.db import get_db
from app.models import Location, ProductVariation, SalesImport, Tenant, User
from app.sales import NotFound
from app.sales_import import (
    get_import,
    mapping_for,
    post,
    preview,
    save_mapping,
    table,
    teach,
    upload,
)
from app.security import require_permission, require_tenant, require_user, require_writable
from app.transform.imports import DATE_FORMATS, ColumnMap, ImportRuleError
from app.transform.sales import ORIGIN_LABELS, SalesRuleError
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()
BASE = "/app/sales/import"


def _act(request: Request, db: Session, action, done: str, back: str) -> RedirectResponse:
    try:
        action()
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except (ImportRuleError, SalesRuleError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(back, status_code=303)
    db.commit()
    flash(request, done)
    return RedirectResponse(back, status_code=303)


@router.get(BASE, response_class=HTMLResponse)
def imports_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("sales.import")),
):
    return templates.TemplateResponse(
        request,
        "app/sales_import.html",
        {
            "title": "Import sales",
            "tenant": tenant,
            "message": pop_flash(request),
            "imports": list(
                db.scalars(
                    select_scoped(org, SalesImport).order_by(SalesImport.id.desc()).limit(20)
                )
            ),
            "places": list(db.scalars(select_scoped(org, Location).order_by(Location.id))),
        },
    )


@router.post(BASE)
def upload_file(
    request: Request,
    location_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_permission("sales.import")),
):
    try:
        row = upload(
            db,
            org,
            location_id=location_id,
            filename=file.filename or "sales.csv",
            content=file.file.read(),
            uploaded_by=user.id,
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except ImportRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(BASE, status_code=303)
    db.commit()
    return RedirectResponse(f"{BASE}/{row.id}", status_code=303)


@router.get(BASE + "/{import_id}", response_class=HTMLResponse)
def import_page(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("sales.import")),
):
    try:
        row = get_import(db, org, import_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    header, _rows = table(row)
    columns = mapping_for(db, org, row)
    shown = None
    problem = None
    if columns is not None:
        try:
            shown = preview(db, org, import_id)
        except ImportRuleError as exc:
            problem = str(exc)
    sizes = [
        v
        for v in db.scalars(select_scoped(org, ProductVariation))
        if v.is_active and v.product.is_active
    ]
    sizes.sort(key=lambda v: (v.product.name, v.name))
    return templates.TemplateResponse(
        request,
        "app/sales_import_review.html",
        {
            "title": f"Import: {row.filename}",
            "tenant": tenant,
            "message": pop_flash(request),
            "row": row,
            "header": header,
            "columns": columns,
            "formats": ("auto", *DATE_FORMATS),
            "preview": shown,
            "problem": problem,
            "sizes": sizes,
            "labels": {v.id: f"{v.product.name} {v.name}" for v in sizes},
            "origins": ORIGIN_LABELS,
        },
    )


@router.post(BASE + "/{import_id}/mapping")
def choose_columns(
    request: Request,
    import_id: int,
    date_column: str = Form(""),
    product_column: str = Form(""),
    quantity_column: str = Form(""),
    date_format: str = Form("auto"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("sales.import")),
):
    columns = ColumnMap(date_column, product_column, quantity_column, date_format or "auto")
    return _act(
        request,
        db,
        lambda: save_mapping(db, org, import_id, columns),
        "Columns saved for files like this one.",
        f"{BASE}/{import_id}",
    )


@router.post(BASE + "/{import_id}/match")
def match(
    request: Request,
    import_id: int,
    name: str = Form(""),
    product_variation_id: int = Form(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("sales.import")),
):
    def act() -> None:
        get_import(db, org, import_id)
        teach(db, org, name, product_variation_id)

    return _act(request, db, act, f"{name} matched.", f"{BASE}/{import_id}")


@router.post(BASE + "/{import_id}/post")
def post_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("sales.import")),
):
    counted: list[int] = []
    return _act(
        request,
        db,
        lambda: counted.append(post(db, org, import_id)),
        "Imported. Days already imported the same way were left as they were.",
        f"{BASE}/{import_id}",
    )
