"""The business history screen (T1.12, ADM-FR-11, AC-ADM-04): who changed what, filterable."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped, team
from app.audit import CATEGORIES, PAGE, history
from app.db import get_db
from app.models import Location, Tenant
from app.security import require_permission, require_tenant
from app.web.templating import templates

router = APIRouter()


@router.get("/app/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    category: str = "",
    person: int | None = None,
    before: str = "",
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("audit.view")),
):
    people = {user.id: user.email for _membership, user in team(db, org)}
    if person is not None and person not in people:
        raise HTTPException(status_code=404, detail="person not found")
    try:
        until = datetime.fromisoformat(before) if before else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="before is not a time") from exc
    rows = history(db, org, category=category, person=person, before=until)
    locations = {loc.id: loc.name for loc in db.scalars(select_scoped(org, Location))}
    return templates.TemplateResponse(
        request,
        "app/audit.html",
        {
            "title": "Business history",
            "tenant": tenant,
            "rows": rows,
            "categories": CATEGORIES,
            "category": category,
            "people": sorted(people.items(), key=lambda item: item[1]),
            "person": person,
            "location_names": locations,
            "older": rows[-1][0].at.isoformat() if len(rows) == PAGE else None,
            "business_wide": org.can("settings.manage"),
        },
    )
