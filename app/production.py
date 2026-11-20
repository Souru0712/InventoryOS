"""Production runs (T2.09; WF-11, PRD-FR-01..08, -12, -14, PD-15, ADR-0060).

The only writer of `production_orders`. A run makes a prepared item or finished good from its
production recipe in effect (a recipe owned by the item), times a multiplier, in one step: one
`production` transaction keyed `production_order:<id>` takes each input (`production_input`,
from its batches soonest-expiry first, ADR-0056) and puts the actual yield into a new batch
(`production_output`) with a readable code and a use-by date. The output's cost is what the
inputs cost over the actual yield, and becomes the item's cost; unknown when an input has none.
Inputs short of stock are allowed, since the making happened: the balance goes below zero and
raises `negative_balance` (PD-15). The recorder voids for 30 minutes; afterwards
`production.void` with a reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.adjustments import negative_finding
from app.batches import new_batch
from app.costs import cost_on, set_cost
from app.exceptions import record_exception
from app.items import entered_base
from app.ledger.posting import balance, post_transaction, reverse_transaction, standing
from app.models import (
    InventoryItem,
    ItemLocation,
    Location,
    ProductionOrder,
    RecipeVersion,
    StockItem,
)
from app.recipes import Owner, effective_version, recipe_of
from app.transform.business_days import business_date
from app.transform.exceptions import Finding
from app.transform.items import ItemRuleError
from app.transform.ledger import Line, places
from app.transform.production import (
    Planned,
    ProductionRuleError,
    batch_code,
    check_yield,
    output_cost,
    plan,
    use_by,
    void_refusal,
)
from app.transform.recipes import RECIPE_ITEM_TYPES

SOURCE = "production_order"
VOID_ANY = "production.void"


class NotFound(LookupError):
    """A production run, item or location outside the business, or not there at all."""


@dataclass(frozen=True)
class Proposal:
    """What a run would take and make, before it is confirmed (PRD-FR-03)."""

    item: InventoryItem
    location: Location
    version: RecipeVersion
    planned: Planned
    on_hand: dict[int, Decimal]  # each input's recorded quantity now
    names: dict[int, InventoryItem]

    @property
    def short(self) -> list[int]:
        """Inputs the run would take below zero (PD-15 warns, it does not refuse)."""
        return [i for i, qty in self.planned.inputs.items() if self.on_hand.get(i, 0) < qty]


def makeable(db: Session, org: OrgContext, location_id: int) -> list[InventoryItem]:
    """Prepared items and finished goods stocked at a location that have a production recipe."""
    stocked = {
        row.inventory_item_id
        for row in db.scalars(
            select_scoped(org, ItemLocation).where(ItemLocation.location_id == location_id)
        )
    }
    items = db.scalars(
        select_scoped(org, InventoryItem)
        .where(InventoryItem.item_type.in_(RECIPE_ITEM_TYPES), InventoryItem.is_active.is_(True))
        .order_by(InventoryItem.name)
    )
    return [i for i in items if i.id in stocked and recipe_of(db, org, Owner("item", i.id))]


def propose(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    inventory_item_id: int,
    multiplier: Decimal,
    at: datetime | None = None,
) -> Proposal:
    """The inputs and expected yield of a run, and what is on hand of each input."""
    place = get_scoped(db, org, Location, location_id)
    item = get_scoped(db, org, InventoryItem, inventory_item_id)
    if place is None or item is None:
        raise NotFound(inventory_item_id)
    if item.item_type not in RECIPE_ITEM_TYPES:
        raise ProductionRuleError("Only prepared items and finished goods are made here.")
    recipe = recipe_of(db, org, Owner("item", item.id))
    version = effective_version(db, org, recipe, at) if recipe is not None else None
    if version is None:
        raise ProductionRuleError(f"{item.name} has no production recipe in effect.")
    planned = plan(
        ((ln.inventory_item_id, Decimal(ln.qty_base)) for ln in version.lines),
        Decimal(version.yield_base),
        multiplier,
    )
    names = {
        i.id: i
        for i in db.scalars(
            select_scoped(org, InventoryItem).where(InventoryItem.id.in_(list(planned.inputs)))
        )
    }
    on_hand = {i: balance(db, org, place.id, i) for i in planned.inputs}
    return Proposal(item, place, version, planned, on_hand, names)


def record_run(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    inventory_item_id: int,
    multiplier: Decimal,
    actual_yield: str = "",
    yield_unit: str = "",
    note: str | None = None,
    now: datetime | None = None,
) -> ProductionOrder:
    """Record and post a run in one step (PRD-FR-03..06, -08, -12). The actual yield, entered in
    any unit of the item, defaults to the expected one."""
    now = now or datetime.now(UTC)
    proposal = propose(
        db,
        org,
        location_id=location_id,
        inventory_item_id=inventory_item_id,
        multiplier=multiplier,
        at=now,
    )
    item, place, planned = proposal.item, proposal.location, proposal.planned
    stocked = {
        row.inventory_item_id
        for row in db.scalars(
            select_scoped(org, ItemLocation).where(ItemLocation.location_id == place.id)
        )
    }
    missing = [proposal.names[i].name for i in planned.inputs if i not in stocked]
    if missing:
        raise ProductionRuleError(f"{', '.join(sorted(missing))} is not stocked at {place.name}.")
    if actual_yield.strip():
        try:
            actual = check_yield(entered_base(item, actual_yield, yield_unit))
        except ItemRuleError as exc:
            raise ProductionRuleError(str(exc)) from exc
    else:
        actual = planned.expected_yield
    stock = db.scalars(
        select_scoped(org, StockItem).where(
            StockItem.location_id == place.id, StockItem.inventory_item_id == item.id
        )
    ).first()
    if stock is None:
        raise ProductionRuleError(f"{item.name} is not stocked at {place.name}.")
    costs = {i: cost_on(db, org, place.id, i, now) for i in planned.inputs}
    unit_cost = output_cost(((qty, costs[i]) for i, qty in planned.inputs.items()), actual)
    day = business_date(now, place.timezone, place.business_day_cutoff_hour)
    runs_today = db.scalars(
        select_scoped(org, ProductionOrder).where(
            ProductionOrder.inventory_item_id == item.id,
            ProductionOrder.location_id == place.id,
            ProductionOrder.occurred_at >= now - timedelta(days=1),
        )
    ).all()
    number = 1 + sum(
        1
        for run in runs_today
        if business_date(run.occurred_at, place.timezone, place.business_day_cutoff_hour) == day
    )
    code = batch_code(item.name, day, number)
    batch = new_batch(
        db,
        org,
        stock,
        actual,
        use_by(day, item.default_shelf_life_days),
        source="production",
        date_type="use_by",
        cost_per_base_cents=unit_cost,
        received_at=now,
    )
    batch.lot_code = code
    order = ProductionOrder(
        tenant_id=org.tenant_id,
        location_id=place.id,
        inventory_item_id=item.id,
        recipe_version_id=proposal.version.id,
        multiplier=places(multiplier),
        expected_yield_base=planned.expected_yield,
        actual_yield_base=actual,
        variance_base=actual - planned.expected_yield,
        unit_cost=unit_cost,
        batch_code=code,
        output_batch_id=batch.id,
        note=(note or "").strip()[:300] or None,
        actor_user_id=org.user_id,
        occurred_at=now,
    )
    db.add(order)
    db.flush()
    why = f"Made {code}"
    lines = [
        Line(
            place.id,
            input_id,
            "production_input",
            -qty,
            unit_cost=costs[input_id] or None,
            reason=why,
        )
        for input_id, qty in sorted(planned.inputs.items())
    ]
    lines.append(
        Line(
            place.id,
            item.id,
            "production_output",
            actual,
            unit_cost=unit_cost if unit_cost is not None else Decimal("0"),
            batch_id=batch.id,
            reason=why,
        )
    )
    posted = post_transaction(
        db,
        org,
        kind="production",
        source_type=SOURCE,
        source_id=str(order.id),
        idempotency_key=f"production_order:{order.id}",
        lines=lines,
        occurred_at=now,
        reason=why,
    )
    order.transaction_id = posted.id
    if unit_cost is not None:
        # What this run's output cost is what replacing it costs (PRD-FR-06).
        set_cost(
            db,
            org,
            location_id=place.id,
            inventory_item_id=item.id,
            cost=unit_cost,
            source_type="production",
            source_id=str(order.id),
            effective_at=now,
        )
    for input_id, cost in costs.items():
        if not cost:
            record_exception(
                db,
                org,
                Finding(
                    type="missing_cost",
                    subject_type="item_location",
                    subject_id=f"{input_id}:{place.id}",
                    title=(
                        f"{proposal.names[input_id].name} went into {item.name} at {place.name} "
                        "with no cost, so the batch's cost is unknown."
                    ),
                    location_id=place.id,
                ),
                now=now,
            )
        after = balance(db, org, place.id, input_id)
        if after < 0:
            record_exception(
                db, org, negative_finding(proposal.names[input_id], place, after), now=now
            )
    db.flush()
    return order


def void_run(
    db: Session, org: OrgContext, order_id: int, *, reason: str = "", now: datetime | None = None
) -> ProductionOrder:
    """Undo a run with a reversal of all its movements; the output batch holds nothing after
    (PRD-FR-07, AC-PRD-05)."""
    order = get_scoped(db, org, ProductionOrder, order_id)
    if order is None:
        raise NotFound(order_id)
    if order.status == "voided":
        raise ProductionRuleError("That run is already voided.")
    now = now or datetime.now(UTC)
    refusal = void_refusal(
        recorded_by=order.actor_user_id,
        recorded_at=order.recorded_at,
        person=org.user_id,
        may_void_any=org.can(VOID_ANY),
        reason=reason,
        now=now,
    )
    if refusal:
        raise ProductionRuleError(refusal)
    why = "Production voided" + (
        f": {reason.strip()}" if reason.strip() else " by the person who recorded it"
    )
    for posting in standing(db, org, SOURCE, str(order.id)):
        reverse_transaction(db, org, posting.id, reason=why[:300], occurred_at=posting.occurred_at)
    order.status = "voided"
    order.voided_at = now
    order.voided_by_user_id = org.user_id
    order.void_reason = reason.strip()[:300] or None
    db.flush()
    return order


def recent_runs(db: Session, org: OrgContext, *, since: datetime) -> list[ProductionOrder]:
    return list(
        db.scalars(
            select_scoped(org, ProductionOrder)
            .where(ProductionOrder.recorded_at >= since)
            .order_by(ProductionOrder.id.desc())
        )
    )
