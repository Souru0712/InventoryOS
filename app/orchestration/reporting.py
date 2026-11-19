"""The weekly summary email (T2.18; ANL-FR-09, AC-ANL-07).

Stock value, last week's waste cost, money at risk, low stock and open exceptions, read from the
ledger and exceptions as the reports read them. It goes to Owners, and to members whose role
sees costs and financial reports, who have not turned it off: the email carries money.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.access.permissions import VIEW_COSTS
from app.access.scoped import team
from app.config import get_settings
from app.costs import value_stock
from app.entitlements import allows
from app.models import EmailLog, Location, Tenant
from app.notify.email import render_pair, send_email, wants
from app.orchestration.dashboard import needs_attention
from app.orchestration.health import expiry_figures
from app.orchestration.views import expiry_positions, stock_views, tenant_currency
from app.reports import consumption_report
from app.security import member_permissions
from app.transform.business_days import business_date
from app.transform.dashboard import DateRange
from app.transform.reports import (
    EXCEPTIONS_SHOWN,
    LOW_STOCK_SHOWN,
    OpenException,
    WeeklySummary,
    low_stock,
    money,
    qty,
    week_bounds,
)

log = logging.getLogger(__name__)


def _today(db: Session, org: OrgContext, now: datetime) -> date:
    """The business's today: its first location's business day, else the date in UTC."""
    place = db.scalars(
        select_scoped(org, Location).where(Location.is_active.is_(True)).order_by(Location.id)
    ).first()
    if place is None:
        return now.date()
    return business_date(now, place.timezone, place.business_day_cutoff_hour)


def weekly_report(
    db: Session, org: OrgContext, today: date | None = None, now: datetime | None = None
) -> WeeklySummary:
    """Last Monday-to-Sunday in each location's business days, and where stock stands now."""
    now = now or datetime.now(UTC)
    start, end = week_bounds(today or _today(db, org, now))
    valuation = value_stock(db, org)
    waste = consumption_report(db, org, DateRange("custom", start, end), now=now)
    figures = expiry_figures(expiry_positions(db, org, now=now), get_settings().expiry_horizon_days)
    low = low_stock(stock_views(db, org))
    open_now = needs_attention(db, org)
    return WeeklySummary(
        period_start=start,
        period_end=end,
        stock_value_cents=valuation.total_cents,
        items_without_cost=len(valuation.without_cost),
        waste_cents=waste.totals_cents["waste"],
        waste_uncosted=any(row.uncosted and row.qty["waste"] > 0 for row in waste.rows.values()),
        at_risk_cents=figures.at_risk_cents + figures.expired_cents,
        low_stock=tuple(low[:LOW_STOCK_SHOWN]),
        low_stock_count=len(low),
        exceptions=tuple(
            OpenException(view.title, view.severity, view.money_impact_cents)
            for view in open_now[:EXCEPTIONS_SHOWN]
        ),
        exceptions_open=len(open_now),
    )


def recipients(db: Session, org: OrgContext) -> list[str]:
    """Owners, and members whose role sees costs and financial reports, who want the weekly
    summary: nobody without cost access is sent money (REC-FR-16)."""
    found = []
    for membership, user in team(db, org):
        if membership.status != "active" or not user.is_active or not wants(user, "weekly_report"):
            continue
        granted = member_permissions(db, membership)
        if membership.role == "owner" or {VIEW_COSTS, "reports.financial"} <= granted:
            found.append(user.email)
    return found


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

    to = recipients(db, org)
    if not to:
        log.info("tenant %s has nobody who wants the weekly report", org.tenant_id)
        return False

    report = weekly_report(db, org, today)
    currency = tenant_currency(db, org)
    html, text = render_pair(
        "email/weekly_report",
        tenant=tenant,
        report=report,
        money=lambda cents: money(cents, currency),
        qty=qty,
        base_url=settings.app_base_url,
    )
    subject = (
        f"{tenant.name}: week of {report.period_start:%d %b} - "
        f"{money(report.waste_cents, currency)} wasted, "
        f"{money(report.at_risk_cents, currency)} at risk"
    )
    sent = False
    for recipient in to:
        sent = (
            send_email(db, org.tenant_id, recipient, subject, html, "weekly_report", text) or sent
        )
    return sent
