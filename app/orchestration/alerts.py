"""Persist stock and expiry alerts, and send the digest owners actually act on."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.entitlements import allows
from app.models import Alert, AlertKind, StockItem, Tenant, User
from app.notify.email import render_pair, send_email, wants
from app.orchestration.views import expiry_positions, stock_views, tenant_currency, velocity_map
from app.transform.alerts import AlertProposal, evaluate_expiries, evaluate_stock
from app.transform.reports import money
from app.units import format_approx, format_qty

log = logging.getLogger(__name__)

EXPIRY_KINDS = {AlertKind.WASTE_RISK, AlertKind.EXPIRED}


def category_of(kind: AlertKind) -> str:
    """Which switch on the settings page decides whether this alert is emailed."""
    return "expiry_alerts" if kind in EXPIRY_KINDS else "stock_alerts"


def all_proposals(db: Session, tenant_id: int, now: datetime) -> list[AlertProposal]:
    """Stock-level alerts and expiry alerts, from one velocity computation."""
    velocity = velocity_map(db, tenant_id, now.date())
    stock = evaluate_stock(stock_views(db, tenant_id), velocity, now)
    expiry = evaluate_expiries(
        expiry_positions(db, tenant_id, now.date(), velocity=velocity),
        get_settings().expiry_horizon_days,
    )
    return stock + expiry


def evaluate_and_store(db: Session, tenant_id: int, now: datetime | None = None) -> dict:
    """Open new alerts, resolve fixed ones. Returns a small stats dict."""
    now = now or datetime.now(UTC)
    proposals = all_proposals(db, tenant_id, now)

    open_alerts = {
        (alert.stock_item_id, alert.kind.value): alert
        for alert in db.scalars(
            select(Alert).where(Alert.tenant_id == tenant_id, Alert.resolved_at.is_(None))
        )
    }
    seen: set[tuple[int, str]] = set()
    opened = 0

    for proposal in proposals:
        key = (proposal.stock_item_id, proposal.kind)
        seen.add(key)
        current = open_alerts.get(key)
        if current is None:
            db.add(
                Alert(
                    tenant_id=tenant_id,
                    stock_item_id=proposal.stock_item_id,
                    kind=AlertKind(proposal.kind),
                    message=proposal.message,
                    qty_base=proposal.qty_base,
                    threshold_base=proposal.threshold_base,
                    days_of_cover=proposal.days_of_cover,
                    value_cents=proposal.value_cents,
                )
            )
            opened += 1
            continue
        current.message = proposal.message
        current.qty_base = proposal.qty_base
        current.days_of_cover = proposal.days_of_cover
        current.value_cents = proposal.value_cents

    resolved = 0
    for key, alert in open_alerts.items():
        if key not in seen:
            alert.resolved_at = now
            resolved += 1

    db.flush()
    return {"open": len(proposals), "opened": opened, "resolved": resolved}


def send_alert_digest(db: Session, tenant_id: int, now: datetime | None = None) -> bool:
    """Email un-notified alerts, at most one digest per cooldown window."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    cooldown = now - timedelta(hours=settings.alert_cooldown_hours)

    pending = list(
        db.scalars(
            select(Alert).where(
                Alert.tenant_id == tenant_id,
                Alert.resolved_at.is_(None),
                Alert.notified_at.is_(None),
            )
        )
    )
    if not pending:
        return False

    recently_notified = db.scalars(
        select(Alert)
        .where(Alert.tenant_id == tenant_id, Alert.notified_at.is_not(None))
        .order_by(Alert.notified_at.desc())
        .limit(1)
    ).first()
    if (
        recently_notified
        and recently_notified.notified_at
        and _aware(recently_notified.notified_at) > cooldown
    ):
        log.info("tenant %s alert digest suppressed by cooldown", tenant_id)
        return False

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise LookupError(f"tenant {tenant_id} has pending alerts but no tenant row")
    if not allows(tenant, "notifications"):
        log.info("tenant %s is not entitled to alerts; digest held", tenant_id)
        return False
    users = list(
        db.scalars(select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True)))
    )
    if not users:
        log.warning("tenant %s has no active users to alert", tenant_id)
        return False

    items = {
        item.id: item
        for item in db.scalars(select(StockItem).where(StockItem.tenant_id == tenant_id))
    }
    # Money first: "$286 may expire" gets the owner to open the app; "7 alerts" does not.
    pending.sort(key=lambda a: (a.kind not in EXPIRY_KINDS, -(a.value_cents or 0)))
    currency = tenant_currency(db, tenant_id)

    sent = False
    for user in users:
        # One digest each, carrying only the categories that person still wants.
        theirs = [alert for alert in pending if wants(user, category_of(alert.kind))]
        if not theirs:
            log.info("tenant %s: user %s wants none of these alert categories", tenant_id, user.id)
            continue

        rows = [
            {
                "name": items[alert.stock_item_id].name if alert.stock_item_id in items else "item",
                "kind": alert.kind.value,
                "message": alert.message,
                "quantity": (format_approx if alert.kind in EXPIRY_KINDS else format_qty)(
                    alert.qty_base, items[alert.stock_item_id].stock_unit
                )
                if alert.stock_item_id in items
                else "",
                "days_of_cover": alert.days_of_cover,
                "value_cents": alert.value_cents,
            }
            for alert in theirs
        ]
        at_risk_cents = sum(
            alert.value_cents or 0 for alert in theirs if alert.kind in EXPIRY_KINDS
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
        sent = send_email(db, tenant_id, user.email, subject, html, "alert_digest", text) or sent

    # Every alert this run considered is marked, whoever ended up receiving it, so the next run
    # raises only what is new.
    for alert in pending:
        alert.notified_at = now
    db.flush()
    return sent


def open_alerts(db: Session, tenant_id: int) -> list[Alert]:
    return list(
        db.scalars(
            select(Alert)
            .where(Alert.tenant_id == tenant_id, Alert.resolved_at.is_(None))
            .order_by(Alert.created_at.desc())
        )
    )


def proposals_for(db: Session, tenant_id: int, now: datetime | None = None) -> list[AlertProposal]:
    now = now or datetime.now(UTC)
    return evaluate_stock(stock_views(db, tenant_id), velocity_map(db, tenant_id, now.date()), now)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


ZERO = Decimal("0")
