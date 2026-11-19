"""Reports (T2.16; ANL-FR-02..05, -07, AC-ANL-03, -05): /app/reports.

Consumption split, count variance and expiry losses over a period of each location's business
days, beside links to the waste report (ANL-FR-04) and as-of valuation (ANL-FR-02). Money shows
only to a role with cost access and financial reports, and never reaches the page otherwise.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.access.permissions import VIEW_COSTS
from app.db import get_db
from app.models import InventoryItem, Location, Tenant
from app.orchestration.views import tenant_currency
from app.reports import consumption_report, days, expiry_report, variance_report
from app.security import location_filter, require_permission, require_tenant
from app.transform.analytics import PARTS
from app.transform.dashboard import DEFAULT_RANGE, DateRange, RangeError, check_range
from app.web.templating import templates

router = APIRouter()


def _day(text: str) -> date | None:
    if not text.strip():
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError as exc:
        raise RangeError("A day is a date like 2026-11-05.") from exc


@router.get("/app/reports", response_class=HTMLResponse)
def reports_page(
    request: Request,
    location: int | None = Depends(location_filter),
    period: str = Query(DEFAULT_RANGE, alias="range"),
    first: str = Query("", alias="from"),
    last: str = Query("", alias="to"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("reports.operational")),
):
    range_error = None
    try:
        chosen = check_range(period, _day(first), _day(last))
    except RangeError as exc:
        chosen, range_error = DateRange(), str(exc)
    now = datetime.now(UTC)
    costs = org.can(VIEW_COSTS) and org.can("reports.financial")
    places = list(db.scalars(select_scoped(org, Location).order_by(Location.id)))
    items = {i.id: i for i in db.scalars(select_scoped(org, InventoryItem))}
    used = consumption_report(db, org, chosen, location_id=location, now=now)
    counted = variance_report(db, org, chosen, location_id=location, now=now)
    expired = expiry_report(db, org, chosen, location_id=location, now=now)

    def by_name(rows):
        """Rows keyed (location, item), in item-name order."""
        return sorted(
            rows.items(),
            key=lambda kv: (items[kv[0][1]].name.lower() if kv[0][1] in items else "", kv[0]),
        )

    return templates.TemplateResponse(
        request,
        "app/reports.html",
        {
            "title": "Reports",
            "tenant": tenant,
            "period": chosen,
            "range_error": range_error,
            "location": location,
            "places": places,
            "names": {p.id: p.name for p in places},
            "spans": days(db, org, chosen, now, location),
            "items": items,
            "parts": PARTS,
            "consumption": used,
            "consumption_rows": by_name(used.rows),
            "variance": counted,
            "variance_rows": by_name(counted.rows),
            "expiry": expired,
            "lost_rows": by_name(expired.lost),
            "costs": costs,
            "currency": tenant_currency(db, org),
        },
    )
