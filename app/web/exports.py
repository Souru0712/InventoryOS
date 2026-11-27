"""Exporting the business's data (T4.05, ADR-0092; ADM-FR-10, SAAS-D-01, SAAS-D-02, AC-ADM-03).

The page asks for an export and lists recent ones; the zip downloads only for the person who
asked, until it expires. Asking works in every subscription state (SAAS-D-02), so it does not
depend on a writable subscription (listed in tests/test_lifecycle.py).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped
from app.db import get_db
from app.exports import ExportRefused, downloadable, history, request_export
from app.models import DataExport, Tenant, User
from app.security import require_permission, require_tenant, require_user
from app.storage import StorageError, get_storage
from app.throttle import client_ip
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()


@router.get("/app/exports", response_class=HTMLResponse)
def exports_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_permission("data.export")),
):
    rows = history(db, org)
    now = datetime.now(UTC)
    return templates.TemplateResponse(
        request,
        "app/exports.html",
        {
            "title": "Export your data",
            "tenant": tenant,
            "message": pop_flash(request),
            "exports": rows,
            "mine": {row.id for row in rows if downloadable(row, user.id, now)},
        },
    )


@router.post("/app/exports")
def ask_for_export(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_permission("data.export")),
):
    try:
        request_export(db, org, user.id, ip=client_ip(request))
    except ExportRefused as exc:
        return templates.TemplateResponse(
            request,
            "app/exports.html",
            {
                "title": "Export your data",
                "tenant": tenant,
                "error": str(exc),
                "exports": [],
                "mine": set(),
            },
            status_code=403,
        )
    db.commit()
    flash(request, "Your export is being prepared. We will email you when it is ready.")
    return RedirectResponse("/app/exports", status_code=303)


@router.get("/app/exports/{export_id}/download")
def download_export(
    export_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_permission("data.export")),
):
    row = get_scoped(db, org, DataExport, export_id)
    if row is None or row.storage_key is None or not downloadable(row, user.id):
        raise HTTPException(status_code=404, detail="export not found or expired")
    try:
        data = get_storage().get(row.storage_key)
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="export file is gone") from exc
    name = f"vontry-export-{row.created_at:%Y%m%d-%H%M}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
