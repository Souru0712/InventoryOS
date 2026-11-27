"""Inventory health, the reasons behind it, and the reorder list it points to."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.access import OrgContext
from app.db import get_db
from app.models import Tenant
from app.orchestration.health import current, over_time
from app.reorder import plan
from app.security import location_filter, require_permission, require_tenant
from app.transform.reorder import (
    DEFAULT_COVER_DAYS,
    DEFAULT_SAFETY_PERCENT,
    DEFAULT_WINDOW,
    WINDOWS,
    ReorderRuleError,
    check_settings,
)
from app.units import format_approx, format_qty
from app.web.templating import templates

router = APIRouter()

HISTORY_DAYS = 30


@router.get("/app/health", response_class=HTMLResponse)
def health_page(
    request: Request,
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("reports.operational")),
):
    now = datetime.now(UTC)
    health = current(db, org, location, now)
    return templates.TemplateResponse(
        request,
        "app/health.html",
        {
            "title": "Inventory health",
            "tenant": tenant,
            "report": health.report,
            "actions": health.actions,
            # The history is core, on every plan (C-41, E-05).
            "trend": over_time(db, org, health.report, now.date(), days=HISTORY_DAYS),
            "by_id": {item.item.id: item for item in health.input.items},
            "currency": health.input.currency,
        },
    )


def _settings(window: int, cover: int, safety: str) -> tuple[int, int, Decimal]:
    try:
        safety_percent = Decimal(safety or DEFAULT_SAFETY_PERCENT)
        check_settings(window, cover, safety_percent)
    except (ArithmeticError, ReorderRuleError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return window, cover, safety_percent


@router.get("/app/reorder", response_class=HTMLResponse)
def reorder_page(
    request: Request,
    window: int = DEFAULT_WINDOW,
    cover: int = DEFAULT_COVER_DAYS,
    safety: str = "",
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("reorder.view")),
):
    """Suggestions with the numbers behind them (ROR-FR-03); nothing is ordered from here."""
    window, cover, safety_percent = _settings(window, cover, safety)
    return templates.TemplateResponse(
        request,
        "app/reorder.html",
        {
            "title": "Reorder",
            "tenant": tenant,
            "groups": plan(
                db,
                org,
                location_id=location,
                window=window,
                cover_days=cover,
                safety_percent=safety_percent,
            ),
            "window": window,
            "cover": cover,
            "safety": safety_percent,
            "windows": WINDOWS,
            "location": location,
        },
    )


def _cell(value: str) -> str:
    """Stop a product name like '=HYPERLINK(...)' running as a formula when the file is opened."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@") else value


@router.get("/app/reorder.csv")
def reorder_csv(
    window: int = DEFAULT_WINDOW,
    cover: int = DEFAULT_COVER_DAYS,
    safety: str = "",
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("data.export")),
):
    """The suggestions as a CSV grouped by supplier (ROR-FR-07, AC-ROR-04), on every plan (E-05)."""
    window, cover, safety_percent = _settings(window, cover, safety)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Supplier",
            "Order by",
            "Item",
            "Location",
            "On hand",
            "Reorder point",
            "Use per day",
            "Based on",
            "Lead time (days)",
            "Cover (days)",
            "Safety %",
            "Suggested",
            "Purchase units",
            "Count first",
        ]
    )
    for found in plan(
        db,
        org,
        location_id=location,
        window=window,
        cover_days=cover,
        safety_percent=safety_percent,
    ):
        for line in found.lines:
            inputs, suggestion = line.inputs, line.suggestion
            writer.writerow(
                [
                    _cell(found.supplier or "No supplier set"),
                    line.order_by.isoformat(),
                    _cell(line.name),
                    _cell(line.location_name),
                    format_qty(inputs.on_hand, line.unit),
                    format_qty(inputs.reorder_point, line.unit),
                    format_approx(inputs.per_day, line.unit),
                    line.usage.note,
                    inputs.lead_days,
                    inputs.cover_days,
                    f"{inputs.safety_percent.normalize():f}",
                    format_qty(suggestion.quantity, line.unit) if suggestion else "",
                    f"{suggestion.packs} {line.purchase_unit or ''}".strip()
                    if suggestion and suggestion.packs is not None
                    else "",
                    line.count_first or "",
                ]
            )
    stamp = datetime.now(UTC).date().isoformat()
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="reorder-{stamp}.csv"'},
    )
