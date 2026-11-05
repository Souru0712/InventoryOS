"""Inventory health, the reasons behind it, and the reorder list it points to."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Tenant
from app.orchestration.health import current, over_time
from app.entitlements import history_days, require
from app.security import require_tenant
from app.transform.health import reorder_lines
from app.units import format_approx, format_qty
from app.web.templating import templates

router = APIRouter()


@router.get("/app/health", response_class=HTMLResponse)
def health_page(
    request: Request,
    location: int | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    now = datetime.now(timezone.utc)
    health = current(db, tenant.id, location, now)
    return templates.TemplateResponse(
        request,
        "app/health.html",
        {
            "title": "Inventory health",
            "tenant": tenant,
            "report": health.report,
            "actions": health.actions,
            # Starter sees today's score; the history behind it is part of Pro.
            "trend": over_time(db, tenant.id, health.report, now.date(), days=history_days(tenant)),
            "history_days": history_days(tenant),
            "by_id": {item.item.id: item for item in health.input.items},
            "currency": health.input.currency,
        },
    )


@router.get("/app/reorder", response_class=HTMLResponse)
def reorder_page(
    request: Request,
    location: int | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    health = current(db, tenant.id, location)
    return templates.TemplateResponse(
        request,
        "app/reorder.html",
        {
            "title": "Reorder",
            "tenant": tenant,
            "groups": reorder_lines(health.input.items),
            "currency": health.input.currency,
        },
    )


def _cell(value: str) -> str:
    """Stop a product name like '=HYPERLINK(...)' running as a formula when the file is opened."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@") else value


@router.get("/app/reorder.csv")
def reorder_csv(
    location: int | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    require(tenant, "reorder_csv")
    health = current(db, tenant.id, location)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Supplier", "Item", "Location", "Reason", "On hand", "Days to stockout", "Order"]
    )
    for supplier, lines in reorder_lines(health.input.items):
        for line in lines:
            item = line.health.item
            days = line.days_to_stockout
            writer.writerow(
                [
                    _cell(supplier or "No supplier set"),
                    _cell(item.name),
                    _cell(item.location_name),
                    line.reason_label,
                    format_qty(item.qty_base, item.stock_unit),
                    "" if days is None else f"{days:.1f}",
                    format_approx(line.order_base, item.stock_unit) if line.order_base > 0 else "",
                ]
            )
    stamp = datetime.now(timezone.utc).date().isoformat()
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="reorder-{stamp}.csv"'},
    )
