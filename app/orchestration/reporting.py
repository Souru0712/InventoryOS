"""Weekly inventory and sales report."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, members, select_scoped
from app.config import get_settings
from app.entitlements import allows
from app.models import EmailLog, Tenant
from app.notify.email import render_pair, send_email, wants
from app.orchestration.alerts import proposals_for
from app.orchestration.views import sale_rows, sold_item_names, stock_views
from app.transform.reports import WeeklyReport, build_weekly_report, money, week_bounds
from app.units import format_qty

log = logging.getLogger(__name__)


def weekly_report(db: Session, org: OrgContext, today: date | None = None) -> WeeklyReport:
    today = today or datetime.now(UTC).date()
    start, end = week_bounds(today)
    return build_weekly_report(
        sales=sale_rows(db, org),
        item_names=sold_item_names(db, org),
        stock=stock_views(db, org),
        alerts=proposals_for(db, org),
        period_start=start,
        period_end=end,
    )


def report_sent_recently(
    db: Session, org: OrgContext, now: datetime | None = None, within_days: int = 6
) -> bool:
    """True when this week's report already went out, or was logged as skipped."""
    now = now or datetime.now(UTC)
    # Six days, not seven, so next week's slot at the same hour is never mistaken for this one.
    entry = db.scalars(
        select_scoped(org, EmailLog)
        .where(
            EmailLog.kind == "weekly_report",
            EmailLog.status.in_(("sent", "skipped")),
            EmailLog.sent_at >= now - timedelta(days=within_days),
        )
        .limit(1)
    ).first()
    return entry is not None


def send_weekly_report(db: Session, org: OrgContext, today: date | None = None) -> bool:
    settings = get_settings()
    tenant = get_scoped(db, org, Tenant, org.tenant_id)
    if tenant is None:
        raise ValueError(f"tenant {org.tenant_id} not found")

    if not allows(tenant, "notifications"):
        log.info("tenant %s is not entitled to the weekly report", org.tenant_id)
        return False

    report = weekly_report(db, org, today)
    items = {item.id: item for item in stock_views(db, org)}
    alert_rows = [
        {
            "name": items[alert.stock_item_id].name if alert.stock_item_id in items else "item",
            "message": alert.message,
            "suggested": format_qty(
                alert.suggested_order_base, items[alert.stock_item_id].stock_unit
            )
            if alert.stock_item_id in items and alert.suggested_order_base > 0
            else "",
        }
        for alert in report.alerts
    ]

    html, text = render_pair(
        "email/weekly_report",
        tenant=tenant,
        report=report,
        alerts=alert_rows,
        money=money,
        base_url=settings.app_base_url,
    )
    subject = (
        f"{tenant.name}: week of {report.period_start:%d %b} - "
        f"{money(report.net_cents)} sales, {len(report.alerts)} stock alerts"
    )

    recipients = [user.email for user in members(db, org) if wants(user, "weekly_report")]
    if not recipients:
        log.info("tenant %s has nobody who wants the weekly report", org.tenant_id)
        return False

    sent = False
    for recipient in recipients:
        sent = (
            send_email(db, org.tenant_id, recipient, subject, html, "weekly_report", text) or sent
        )
    return sent
