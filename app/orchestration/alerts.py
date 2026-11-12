"""Evaluate stock and expiry into exceptions, and send the digest owners actually act on.

The five alert kinds are exceptions now (T1.19, DM-8, ADR-0043): this run opens, refreshes and
auto-resolves them through app/exceptions.py, and the digest emails each member only what their
role and locations may see (REC-FR-16).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped, team
from app.access.permissions import VIEW_COSTS
from app.config import get_settings
from app.entitlements import allows
from app.exceptions import apply_findings, mark_notified
from app.models import ExceptionRecord, StockItem, Tenant
from app.notify.email import render_pair, send_email, wants
from app.orchestration.views import expiry_positions, stock_views, tenant_currency, velocity_map
from app.transform.alerts import AlertProposal, evaluate_expiries, evaluate_stock
from app.transform.exceptions import (
    EXPIRY_TYPES,
    OPEN_STATUSES,
    STOCK_TYPES,
    from_alert,
    visible_to,
)
from app.transform.reports import money
from app.units import format_approx, format_qty

log = logging.getLogger(__name__)


def category_of(exception_type: str) -> str:
    """Which switch on the settings page decides whether this exception is emailed."""
    return "expiry_alerts" if exception_type in EXPIRY_TYPES else "stock_alerts"


def all_proposals(db: Session, org: OrgContext, now: datetime) -> list[AlertProposal]:
    """Stock-level alerts and expiry alerts, from one velocity computation."""
    velocity = velocity_map(db, org, now.date())
    stock = evaluate_stock(stock_views(db, org), velocity, now)
    expiry = evaluate_expiries(
        expiry_positions(db, org, velocity=velocity, now=now),
        get_settings().expiry_horizon_days,
    )
    return stock + expiry


def evaluate_and_store(db: Session, org: OrgContext, now: datetime | None = None) -> dict:
    """Open new stock and expiry exceptions, refresh current ones, and resolve, as the system,
    those whose condition has cleared (REC-FR-13, -14). Returns a small stats dict."""
    now = now or datetime.now(UTC)
    places = {item.id: item.location_id for item in db.scalars(select_scoped(org, StockItem))}
    findings = [
        from_alert(proposal, places.get(proposal.stock_item_id))
        for proposal in all_proposals(db, org, now)
    ]
    return apply_findings(db, org, findings, STOCK_TYPES, now)


def send_alert_digest(db: Session, org: OrgContext, now: datetime | None = None) -> bool:
    """Email un-notified stock and expiry exceptions, at most one digest per cooldown window."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    cooldown = now - timedelta(hours=settings.alert_cooldown_hours)
    ours = ExceptionRecord.type.in_(sorted(STOCK_TYPES))

    pending = list(
        db.scalars(
            select_scoped(org, ExceptionRecord).where(
                ours,
                ExceptionRecord.status.in_(OPEN_STATUSES),
                ExceptionRecord.notified_at.is_(None),
            )
        )
    )
    if not pending:
        return False

    recently_notified = db.scalars(
        select_scoped(org, ExceptionRecord)
        .where(ours, ExceptionRecord.notified_at.is_not(None))
        .order_by(ExceptionRecord.notified_at.desc())
        .limit(1)
    ).first()
    if (
        recently_notified
        and recently_notified.notified_at
        and recently_notified.notified_at > cooldown
    ):
        log.info("tenant %s alert digest suppressed by cooldown", org.tenant_id)
        return False

    tenant = get_scoped(db, org, Tenant, org.tenant_id)
    if tenant is None:
        raise LookupError(f"tenant {org.tenant_id} has pending alerts but no tenant row")
    if not allows(tenant, "notifications"):
        log.info("tenant %s is not entitled to alerts; digest held", org.tenant_id)
        return False
    recipients = [
        (OrgContext.for_membership(membership), user)
        for membership, user in team(db, org)
        if membership.status == "active" and user.is_active
    ]
    if not recipients:
        log.warning("tenant %s has no active users to alert", org.tenant_id)
        return False

    items = {item.id: item for item in db.scalars(select_scoped(org, StockItem))}
    # Money first: "$286 may expire" gets the owner to open the app; "7 alerts" does not.
    pending.sort(key=lambda e: (e.type not in EXPIRY_TYPES, -(e.money_impact_cents or 0)))
    currency = tenant_currency(db, org)

    sent = False
    for member, user in recipients:
        # One digest each: the categories that person still wants, and only what their role and
        # locations may see, amounts included (REC-FR-16).
        costs = member.can(VIEW_COSTS)
        theirs = [
            exception
            for exception in pending
            if wants(user, category_of(exception.type))
            and visible_to(exception.type, costs)
            and (member.location_ids is None or exception.location_id in member.location_ids)
        ]
        if not theirs:
            log.info(
                "tenant %s: user %s wants none of these alert categories", org.tenant_id, user.id
            )
            continue

        rows = []
        for exception in theirs:
            item = items.get(int(exception.subject_id))
            unit_format = format_approx if exception.type in EXPIRY_TYPES else format_qty
            rows.append(
                {
                    "name": item.name if item else "item",
                    "kind": exception.type,
                    "message": exception.title,
                    "quantity": unit_format(exception.quantity_base, item.stock_unit)
                    if item and exception.quantity_base is not None
                    else "",
                    "days_of_cover": exception.days_of_cover,
                    "value_cents": exception.money_impact_cents if costs else None,
                }
            )
        at_risk_cents = (
            sum(
                exception.money_impact_cents or 0
                for exception in theirs
                if exception.type in EXPIRY_TYPES
            )
            if costs
            else 0
        )
        count = f"{len(rows)} alert{'s' if len(rows) != 1 else ''}"
        if at_risk_cents:
            subject = f"{money(at_risk_cents, currency)} of stock at risk, {count} - {tenant.name}"
        else:
            subject = f"{len(rows)} stock alert{'s' if len(rows) != 1 else ''} - {tenant.name}"
        html, text = render_pair(
            "email/alert_digest",
            tenant=tenant,
            rows=rows,
            at_risk_cents=at_risk_cents,
            currency=currency,
            money=money,
            base_url=settings.app_base_url,
        )
        sent = (
            send_email(db, org.tenant_id, user.email, subject, html, "alert_digest", text) or sent
        )

    # Every exception this run considered is marked, whoever ended up receiving it, so the
    # next run raises only what is new.
    mark_notified(pending, now)
    db.flush()
    return sent


def proposals_for(db: Session, org: OrgContext, now: datetime | None = None) -> list[AlertProposal]:
    now = now or datetime.now(UTC)
    return evaluate_stock(stock_views(db, org), velocity_map(db, org, now.date()), now)


ZERO = Decimal("0")
