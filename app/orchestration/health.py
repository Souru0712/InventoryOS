"""Inventory health from live stock: the score, today's actions, and the nightly history."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.config import get_settings
from app.models import HealthSnapshot
from app.orchestration.promotions import suggestions_for
from app.orchestration.views import (
    expiry_coverage,
    expiry_positions,
    sales_signal,
    stock_views,
    tenant_currency,
    velocity_map,
)
from app.transform.alerts import counts_as_waste_risk
from app.transform.expiry import ItemExpiry
from app.transform.health import (
    ActionCenter,
    Benchmark,
    Change,
    ExpiryFigures,
    HealthInput,
    HealthReport,
    ItemHealth,
    build_actions,
    explain_change,
    score_inventory,
    versus_average,
)

log = logging.getLogger(__name__)

ZERO = Decimal("0")
HISTORY_DAYS = 30


def expiry_figures(expiries: list[ItemExpiry], horizon_days: int) -> ExpiryFigures:
    """The money at stake, by the same rule the alerts and the Expiring page use."""
    at_risk_cents = expired_cents = 0
    at_risk_items: set[int] = set()
    expired_items: set[int] = set()
    for expiry in expiries:
        for position in expiry.positions:
            if position.remaining_base <= ZERO:
                continue
            if position.expired:
                expired_cents += position.at_risk_cents
                expired_items.add(expiry.item.id)
            elif counts_as_waste_risk(position, horizon_days):
                at_risk_cents += position.at_risk_cents
                at_risk_items.add(expiry.item.id)
    return ExpiryFigures(
        at_risk_cents=at_risk_cents,
        at_risk_products=len(at_risk_items),
        expired_cents=expired_cents,
        expired_products=len(expired_items),
        dated_items=len(expiries),
        item_ids=frozenset(at_risk_items | expired_items),
    )


def health_input(
    db: Session, org: OrgContext, now: datetime, location_id: int | None = None
) -> HealthInput:
    today = now.date()
    velocity = velocity_map(db, org, today)
    signal = sales_signal(db, org)
    items = [
        ItemHealth(view, velocity.get(view.id, ZERO), view.id in signal)
        for view in stock_views(db, org, location_id)
    ]

    # Expiry is judged on each location's business day, not UTC's (AC-ADM-01).
    expiries = expiry_positions(db, org, location_id=location_id, velocity=velocity, now=now)
    coverage = expiry_coverage(db, org, location_id)
    suggestions = suggestions_for(db, org, location_id=location_id, now=now)
    return HealthInput(
        items=items,
        expiry=expiry_figures(expiries, get_settings().expiry_horizon_days),
        coverage_percent=coverage.percent if coverage.eligible_items else None,
        needs_dates=len(coverage.needs_dates),
        promotable_urgent=sum(1 for s in suggestions if s.urgency == "critical"),
        promotable=len(suggestions),
        currency=tenant_currency(db, org),
    )


@dataclass(frozen=True)
class Health:
    report: HealthReport
    actions: ActionCenter
    input: HealthInput


def current(
    db: Session, org: OrgContext, location_id: int | None = None, now: datetime | None = None
) -> Health:
    now = now or datetime.now(UTC)
    inputs = health_input(db, org, now, location_id)
    return Health(score_inventory(inputs, now), build_actions(inputs), inputs)


def record_snapshot(db: Session, org: OrgContext, now: datetime | None = None) -> HealthSnapshot:
    """Keep today's health. Running it twice in a night replaces the row rather than adding one."""
    now = now or datetime.now(UTC)
    report = current(db, org, now=now).report
    row = db.scalars(
        select_scoped(org, HealthSnapshot).where(HealthSnapshot.dt == now.date())
    ).first()
    if row is None:
        row = HealthSnapshot(tenant_id=org.tenant_id, dt=now.date())
        db.add(row)
    row.score = report.score
    row.band = report.band
    row.detail_json = json.dumps(report.to_snapshot())
    db.flush()
    return row


@dataclass(frozen=True)
class OverTime:
    change: Change | None
    compared_with: date | None
    benchmark: Benchmark | None
    history: list[HealthSnapshot]  # oldest first


def over_time(
    db: Session, org: OrgContext, report: HealthReport, today: date, days: int = HISTORY_DAYS
) -> OverTime:
    """Today against the last night on record, and against the last `days` of them.

    days is what the plan allows: Pro keeps thirty, Starter sees today's score by itself.
    """
    if days <= 0:
        return OverTime(change=None, compared_with=None, benchmark=None, history=[])
    prior = db.scalars(
        select_scoped(org, HealthSnapshot)
        .where(HealthSnapshot.dt < today)
        .order_by(HealthSnapshot.dt.desc())
        .limit(1)
    ).first()

    change = None
    if prior is not None:
        try:
            change = explain_change(json.loads(prior.detail_json), report.to_snapshot())
        except (ValueError, TypeError, KeyError):
            # A damaged row should cost the comparison, not the whole dashboard.
            log.exception(
                "health snapshot %s for tenant %s could not be read", prior.id, org.tenant_id
            )

    window = list(
        db.scalars(
            select_scoped(org, HealthSnapshot)
            .where(
                HealthSnapshot.dt >= today - timedelta(days=days),
                HealthSnapshot.dt < today,
            )
            .order_by(HealthSnapshot.dt)
        )
    )
    scores = [row.score for row in window if row.score is not None]
    return OverTime(
        change=change,
        compared_with=prior.dt if prior else None,
        benchmark=versus_average(report.score, scores),
        history=window,
    )
