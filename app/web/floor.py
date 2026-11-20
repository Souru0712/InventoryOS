"""The employee home (T2.21; WF-23, UX-02, UX-03, PD-14): /app/floor.

Today's alerts on top, then the floor tasks the role allows, each one tap from here and two at
most to its form. On a shared device (an Employee account) the person working is picked once and
named on every record the device makes until changed. No money appears: alerts come through the
exception views, which hide amounts from a role without cost access.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext
from app.access.scoped import team
from app.db import get_db
from app.models import Membership, Tenant
from app.orchestration.dashboard import needs_attention
from app.security import (
    FLOOR_PERSON,
    floor_person,
    require_membership,
    require_permission,
    require_tenant,
)
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()
BASE = "/app/floor"
ALERT_TYPES = frozenset(
    {"out_of_stock", "low_stock", "waste_risk", "batch_expired_unresolved", "expiring_soon"}
)
ALERTS_SHOWN = 5
# (permission, title, what it is for, where it goes): WF-23's day, in the order it happens.
TASKS = (
    ("receiving.record", "Receive a delivery", "Check it in from the invoice", "/app/deliveries"),
    ("production.record", "Make a batch", "Record what you made", "/app/production"),
    ("waste.record", "Log waste", "Spills, remakes, anything thrown away", "/app/waste"),
    ("inventory.view", "Expiring", "Use first, mark down or throw away", "/app/expiring"),
    ("count.record", "Count", "Count a shelf or the whole store", "/app/counts"),
    ("sales.record", "Today's sales", "Enter what sold today", "/app/sales/daily"),
    ("sales.record", "Usage", "Staff meals and samples", "/app/usage"),
)


@router.get(BASE, response_class=HTMLResponse)
def floor_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    membership: Membership = Depends(require_membership),
    org: OrgContext = Depends(require_permission("inventory.view")),
):
    shared = membership.role == "employee"
    people = (
        [
            (member, user)
            for member, user in team(db, org)
            if member.status == "active" and user.is_active
        ]
        if shared
        else []
    )
    working = floor_person(request, db, membership) or membership.user_id
    alerts = [view for view in needs_attention(db, org) if view.type in ALERT_TYPES]
    return templates.TemplateResponse(
        request,
        "app/floor.html",
        {
            "title": "Today",
            "tenant": tenant,
            "message": pop_flash(request),
            "tasks": [task for task in TASKS if org.can(task[0])],
            "alerts": alerts[:ALERTS_SHOWN],
            "alerts_total": len(alerts),
            "shared": shared and len(people) > 1,
            "people": people,
            "working": working,
        },
    )


@router.post(BASE + "/person")
def pick_person(
    request: Request,
    membership_id: int = Form(0),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require_membership),
    org: OrgContext = Depends(require_permission("inventory.view")),
):
    """Who is working this shared device (PD-14); 0 goes back to the account's own person."""
    key = f"{FLOOR_PERSON}:{org.tenant_id}"
    if membership_id == 0:
        request.session.pop(key, None)
        return RedirectResponse(BASE, status_code=303)
    chosen = next(
        (
            (member, user)
            for member, user in team(db, org)
            if member.id == membership_id and member.status == "active" and user.is_active
        ),
        None,
    )
    if chosen is None:
        raise HTTPException(status_code=404, detail="not found")
    if membership.role != "employee":
        flash(request, "Only a shared floor account picks who is working.", "error")
        return RedirectResponse(BASE, status_code=303)
    request.session[key] = chosen[1].id
    flash(request, f"Records from this device now name {chosen[1].email}.")
    return RedirectResponse(BASE, status_code=303)
