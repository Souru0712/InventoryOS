"""Suggest markdowns from what is actually on the shelf, and remember what was decided.

Suggestions are never stored. They are recomputed from stock every time, so a row can never
go on recommending something the shelf has already moved past. Only decisions persist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Promotion, PromotionStatus, StockBatch, StockItem
from app.orchestration.views import consumption, expiry_positions, velocity_map
from app.transform.inventory import daily_velocity
from app.transform.promotions import PromotionInput, Suggestion, effectiveness, suggest_all

log = logging.getLogger(__name__)

ZERO = Decimal("0")
DECIDED = (PromotionStatus.ACTIVE, PromotionStatus.DISMISSED, PromotionStatus.SNOOZED)


@dataclass(frozen=True)
class RunningPromotion:
    """A markdown in progress, with how sales have moved since it started."""

    promotion: Promotion
    item: StockItem
    velocity_during: Decimal | None
    lift: Decimal | None
    days_running: int


def _open_decisions(db: Session, tenant_id: int, today: date) -> set[int]:
    """Items already spoken for: running, dismissed, or snoozed until later."""
    spoken_for = set()
    for row in db.scalars(
        select(Promotion).where(Promotion.tenant_id == tenant_id, Promotion.status.in_(DECIDED))
    ):
        if row.status is PromotionStatus.SNOOZED and row.snooze_until and row.snooze_until <= today:
            continue  # the snooze has run out, so it can be suggested again
        spoken_for.add(row.stock_item_id)
    return spoken_for


def suggestions_for(
    db: Session, tenant_id: int, today: date | None = None, location_id: int | None = None
) -> list[Suggestion]:
    """What is worth marking down right now, biggest loss first."""
    today = today or datetime.now(timezone.utc).date()
    velocity = velocity_map(db, tenant_id, today)
    already = _open_decisions(db, tenant_id, today)

    inputs: list[PromotionInput] = []
    for expiry in expiry_positions(db, tenant_id, today, location_id, velocity=velocity):
        item = expiry.item
        if item.id in already or not item.retail_per_base_cents:
            continue
        # One suggestion per item: the shelf price is per product, not per lot, so the
        # soonest lot that is not going to sell is the one that sets the urgency.
        at_risk = [
            position
            for position in expiry.positions
            if not position.expired and position.remaining_base > ZERO
        ]
        if not at_risk:
            continue
        position = min(at_risk, key=lambda p: p.days_left)
        inputs.append(
            PromotionInput(
                stock_item_id=item.id,
                name=item.name,
                stock_unit=item.stock_unit,
                remaining_base=position.remaining_base,
                projected_leftover_base=position.projected_leftover_base,
                days_left=position.days_left,
                velocity_per_day=velocity.get(item.id),
                cost_per_base_cents=item.cost_per_base_cents,
                retail_per_base_cents=item.retail_per_base_cents,
                batch_id=position.batch.id,
            )
        )
    return suggest_all(inputs)


def _record(
    db: Session,
    tenant_id: int,
    suggestion: Suggestion,
    status: PromotionStatus,
    user_id: int | None,
    accepted_price_cents: int | None = None,
    snooze_until: date | None = None,
    today: date | None = None,
) -> Promotion:
    today = today or datetime.now(timezone.utc).date()
    item = suggestion.item
    batch = db.get(StockBatch, item.batch_id) if item.batch_id else None
    promotion = Promotion(
        tenant_id=tenant_id,
        stock_item_id=item.stock_item_id,
        stock_batch_id=item.batch_id,
        status=status,
        urgency=suggestion.urgency,
        normal_price_cents=suggestion.normal_price_cents,
        suggested_price_cents=suggestion.suggested_price_cents,
        accepted_price_cents=accepted_price_cents,
        units_at_risk_base=suggestion.units_at_risk_base,
        cost_at_risk_cents=suggestion.cost_at_risk_cents,
        required_velocity=suggestion.required_velocity,
        velocity_before=suggestion.current_velocity,
        units_before_base=item.remaining_base,
        starts_on=today if status is PromotionStatus.ACTIVE else None,
        ends_on=batch.expires_on if batch else today + timedelta(days=item.days_left),
        snooze_until=snooze_until,
        reason="\n".join(suggestion.reasons),
        decided_by=user_id,
    )
    db.add(promotion)
    db.flush()
    return promotion


def accept(
    db: Session,
    tenant_id: int,
    suggestion: Suggestion,
    price_cents: int,
    user_id: int | None = None,
    today: date | None = None,
) -> Promotion:
    """Start a markdown at the price the owner settled on, which may not be the suggested one."""
    return _record(
        db,
        tenant_id,
        suggestion,
        PromotionStatus.ACTIVE,
        user_id,
        accepted_price_cents=price_cents,
        today=today,
    )


def dismiss(
    db: Session, tenant_id: int, suggestion: Suggestion, user_id: int | None = None
) -> Promotion:
    return _record(db, tenant_id, suggestion, PromotionStatus.DISMISSED, user_id)


def snooze(
    db: Session,
    tenant_id: int,
    suggestion: Suggestion,
    user_id: int | None = None,
    days: int = 1,
    today: date | None = None,
) -> Promotion:
    """Ask again later. The suggestion is recomputed then, against whatever the shelf holds."""
    today = today or datetime.now(timezone.utc).date()
    return _record(
        db,
        tenant_id,
        suggestion,
        PromotionStatus.SNOOZED,
        user_id,
        snooze_until=today + timedelta(days=days),
        today=today,
    )


def end(db: Session, promotion: Promotion, today: date | None = None) -> Promotion:
    """Close a markdown and record where the stock ended up, for the comparison afterwards."""
    item = db.get(StockItem, promotion.stock_item_id)
    promotion.status = PromotionStatus.ENDED
    promotion.units_after_base = Decimal(item.qty_base or 0) if item else None
    promotion.ended_at = datetime.now(timezone.utc)
    db.flush()
    return promotion


def _velocity_since(
    db: Session, tenant_id: int, stock_item_id: int, start: date, today: date
) -> Decimal | None:
    """Sales a day for one item over the days a markdown has been running."""
    days = (today - start).days + 1
    if days <= 0:
        return None
    daily = consumption(db, tenant_id, start).get(stock_item_id, {})
    return daily_velocity(daily, days, today)


def running(db: Session, tenant_id: int, today: date | None = None) -> list[RunningPromotion]:
    """Markdowns in progress, each with what has happened to its sales since."""
    today = today or datetime.now(timezone.utc).date()
    rows = db.scalars(
        select(Promotion).where(
            Promotion.tenant_id == tenant_id, Promotion.status == PromotionStatus.ACTIVE
        )
    )
    result = []
    for promotion in rows:
        item = db.get(StockItem, promotion.stock_item_id)
        if item is None:
            continue
        start = promotion.starts_on or today
        during = _velocity_since(db, tenant_id, item.id, start, today)
        result.append(
            RunningPromotion(
                promotion=promotion,
                item=item,
                velocity_during=during,
                lift=effectiveness(promotion.velocity_before, during),
                days_running=max((today - start).days, 0),
            )
        )
    return result


def find_suggestion(suggestions: list[Suggestion], stock_item_id: int) -> Suggestion | None:
    return next((s for s in suggestions if s.item.stock_item_id == stock_item_id), None)
