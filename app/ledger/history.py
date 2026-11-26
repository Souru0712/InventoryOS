"""Movement history (T1.22; INV-FR-20, AC-INV-08): what moved one item at one location, by
whom, why, and the balance after each movement.

Read-only. The running balance is worked out over every movement in posting order, the order
the balance itself moved in, before any filter is applied, so a filtered view still shows true
balances.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.models import InventoryMovement, InventoryTransaction, Location
from app.transform.business_days import business_date
from app.transform.dashboard import movement_value_cents


@dataclass(frozen=True)
class HistoryRow:
    movement_id: int
    occurred_at: datetime
    business_day: date
    kind: str  # the transaction's kind: receipt, count, adjustment...
    movement_type: str
    qty_base: Decimal
    running_base: Decimal  # the balance after this movement
    unit_cost: Decimal  # cents per base unit, as posted
    actor_user_id: int | None
    reason: str | None
    source_type: str
    source_id: str
    allocation_mode: str = "none"  # how its batch was chosen: explicit, inferred or none


def history(
    db: Session,
    org: OrgContext,
    location: Location,
    inventory_item_id: int,
    *,
    movement_type: str | None = None,
    since: date | None = None,
    until: date | None = None,
    actor_user_id: int | None = None,
) -> list[HistoryRow]:
    """The item's movements at the location, newest first, each with the balance after it.
    Dates are the location's business days (ADR-0027)."""
    rows = db.execute(
        select_scoped(org, InventoryMovement)
        .add_columns(InventoryTransaction)
        .join(InventoryTransaction, InventoryTransaction.id == InventoryMovement.transaction_id)
        .where(
            InventoryMovement.location_id == location.id,
            InventoryMovement.inventory_item_id == inventory_item_id,
        )
        .order_by(InventoryMovement.id)
    ).all()
    running = Decimal("0")
    found: list[HistoryRow] = []
    for movement, transaction in rows:
        running += Decimal(movement.qty_base)
        found.append(
            HistoryRow(
                movement_id=movement.id,
                occurred_at=movement.occurred_at,
                business_day=business_date(
                    movement.occurred_at, location.timezone, location.business_day_cutoff_hour
                ),
                kind=transaction.kind,
                movement_type=movement.movement_type,
                qty_base=Decimal(movement.qty_base),
                running_base=running,
                unit_cost=Decimal(movement.unit_cost_snapshot),
                actor_user_id=transaction.actor_user_id,
                reason=movement.reason or transaction.reason,
                source_type=transaction.source_type,
                source_id=transaction.source_id,
                allocation_mode=movement.allocation_mode,
            )
        )
    shown = [
        row
        for row in found
        if (movement_type is None or row.movement_type == movement_type)
        and (since is None or row.business_day >= since)
        and (until is None or row.business_day <= until)
        and (actor_user_id is None or row.actor_user_id == actor_user_id)
    ]
    return list(reversed(shown))


def batch_history(
    db: Session, org: OrgContext, location: Location, batch_id: int
) -> list[HistoryRow]:
    """Every movement that names a batch, newest first, each with what the batch held after it
    (BAT-FR-12, AC-BAT-08)."""
    rows = db.execute(
        select_scoped(org, InventoryMovement)
        .add_columns(InventoryTransaction)
        .join(InventoryTransaction, InventoryTransaction.id == InventoryMovement.transaction_id)
        .where(InventoryMovement.batch_id == batch_id)
        .order_by(InventoryMovement.id)
    ).all()
    running = Decimal("0")
    found: list[HistoryRow] = []
    for movement, transaction in rows:
        running += Decimal(movement.qty_base)
        found.append(
            HistoryRow(
                movement_id=movement.id,
                occurred_at=movement.occurred_at,
                business_day=business_date(
                    movement.occurred_at, location.timezone, location.business_day_cutoff_hour
                ),
                kind=transaction.kind,
                movement_type=movement.movement_type,
                qty_base=Decimal(movement.qty_base),
                running_base=running,
                unit_cost=Decimal(movement.unit_cost_snapshot),
                actor_user_id=transaction.actor_user_id,
                reason=movement.reason or transaction.reason,
                source_type=transaction.source_type,
                source_id=transaction.source_id,
                allocation_mode=movement.allocation_mode,
            )
        )
    return list(reversed(found))


def snapshot_cost_at(
    db: Session, org: OrgContext, location_id: int, inventory_item_id: int, at: datetime
) -> Decimal | None:
    """The unit cost on the last movement posted for the item at the location by an instant: what
    an as-of valuation falls back on before the item had any cost history (ANL-FR-01)."""
    row = db.scalars(
        select_scoped(org, InventoryMovement)
        .where(
            InventoryMovement.location_id == location_id,
            InventoryMovement.inventory_item_id == inventory_item_id,
            InventoryMovement.occurred_at <= at,
        )
        .order_by(InventoryMovement.occurred_at.desc(), InventoryMovement.id.desc())
        .limit(1)
    ).first()
    return Decimal(row.unit_cost_snapshot) if row is not None else None


@dataclass(frozen=True)
class PeriodMovement:
    """One movement in a period, with the record it came from: what reports read (DR-33)."""

    source_type: str
    source_id: str
    location_id: int
    inventory_item_id: int
    movement_type: str
    qty_base: Decimal
    unit_cost: Decimal  # cents per base unit, as posted
    occurred_at: datetime | None = None
    actor_user_id: int | None = None
    reason: str | None = None


def movements_in(
    db: Session,
    org: OrgContext,
    windows: Mapping[int, tuple[datetime, datetime]],
    *,
    source_type: str | None = None,
    movement_type: str | None = None,
) -> list[PeriodMovement]:
    """The movements at each location within its own window (first and last instant, both
    included), from one kind of record or of one type. A reversal carries the record of what it
    undid, so a voided record nets to nothing."""
    found: list[PeriodMovement] = []
    for location_id, (start, end) in windows.items():
        query = (
            select_scoped(org, InventoryMovement)
            .add_columns(InventoryTransaction)
            .join(InventoryTransaction, InventoryTransaction.id == InventoryMovement.transaction_id)
            .where(
                InventoryMovement.location_id == location_id,
                InventoryMovement.occurred_at >= start,
                InventoryMovement.occurred_at <= end,
            )
        )
        if source_type is not None:
            query = query.where(InventoryTransaction.source_type == source_type)
        if movement_type is not None:
            query = query.where(InventoryMovement.movement_type == movement_type)
        for movement, transaction in db.execute(query.order_by(InventoryMovement.id)):
            found.append(
                PeriodMovement(
                    transaction.source_type,
                    transaction.source_id,
                    movement.location_id,
                    movement.inventory_item_id,
                    movement.movement_type,
                    Decimal(movement.qty_base),
                    Decimal(movement.unit_cost_snapshot),
                    movement.occurred_at,
                    transaction.actor_user_id,
                    movement.reason or transaction.reason,
                )
            )
    return found


@dataclass(frozen=True)
class MovementTotal:
    """What moved of one kind over a period: how many movements and their value as posted."""

    movements: int = 0
    value_cents: int = 0  # signed as the stock moved: waste is negative


def movement_totals(
    db: Session,
    org: OrgContext,
    windows: Mapping[int, tuple[datetime, datetime]],
    movement_types: Iterable[str],
) -> dict[str, MovementTotal]:
    """Movements of the given types per type, each location over its own window (first and last
    instant, both included), valued at each movement's own snapshot cost (DR-33, AC-ANL-01). A
    reversal in the window counts against the type it reversed, so undone waste is not waste."""
    wanted = sorted(set(movement_types))
    totals = {kind: MovementTotal() for kind in wanted}
    columns = (
        InventoryMovement.movement_type,
        InventoryMovement.qty_base,
        InventoryMovement.unit_cost_snapshot,
        InventoryMovement.reverses_movement_id,
    )
    found: list[tuple[str, Decimal, Decimal, int | None]] = []
    for location_id, (start, end) in windows.items():
        rows = db.execute(
            select_scoped(org, InventoryMovement)
            .with_only_columns(*columns)
            .where(
                InventoryMovement.location_id == location_id,
                InventoryMovement.movement_type.in_([*wanted, "reversal"]),
                InventoryMovement.occurred_at >= start,
                InventoryMovement.occurred_at <= end,
            )
        )
        found.extend((kind, qty, cost, undone) for kind, qty, cost, undone in rows)
    reversed_ids = {undone for kind, _, _, undone in found if kind == "reversal" and undone}
    original: dict[int, str] = {}
    if reversed_ids:
        undone_rows = db.execute(
            select_scoped(org, InventoryMovement)
            .with_only_columns(InventoryMovement.id, InventoryMovement.movement_type)
            .where(InventoryMovement.id.in_(reversed_ids))
        )
        original = {movement_id: kind for movement_id, kind in undone_rows}
    for kind, qty, cost, undone in found:
        counted = original.get(undone) if kind == "reversal" and undone else kind
        if counted not in totals:
            continue
        before = totals[counted]
        totals[counted] = MovementTotal(
            before.movements + (-1 if kind == "reversal" else 1),
            before.value_cents + movement_value_cents(Decimal(qty), Decimal(cost or 0)),
        )
    return totals


@dataclass(frozen=True)
class ItemMove:
    """One movement of an item, as usage and count-based usage read it (ROR-FR-01, AC-ANL-08)."""

    location_id: int
    inventory_item_id: int
    occurred_at: datetime
    transaction_id: int
    kind: str  # the transaction's kind: sale, count, receipt...
    source_type: str
    movement_type: str  # a reversal carries the type of the movement it undid
    qty_base: Decimal
    unit_cost: Decimal = Decimal(0)  # cents per base unit, as posted
    reverses: bool = False
    source_id: str = ""


def item_moves(
    db: Session, org: OrgContext, windows: Mapping[int, tuple[datetime, datetime]]
) -> list[ItemMove]:
    """Every movement at each location within its window (both ends included), in the order
    stock moved. A reversal takes the type of what it undid, so the two net out by type."""
    found: list[tuple[InventoryMovement, InventoryTransaction]] = []
    for location_id, (start, end) in windows.items():
        found.extend(
            db.execute(
                select_scoped(org, InventoryMovement)
                .add_columns(InventoryTransaction)
                .join(
                    InventoryTransaction,
                    InventoryTransaction.id == InventoryMovement.transaction_id,
                )
                .where(
                    InventoryMovement.location_id == location_id,
                    InventoryMovement.occurred_at >= start,
                    InventoryMovement.occurred_at <= end,
                )
            ).tuples()
        )
    undone = {m.reverses_movement_id for m, _ in found if m.reverses_movement_id is not None}
    original: dict[int, str] = {}
    if undone:
        original = {
            movement_id: kind
            for movement_id, kind in db.execute(
                select_scoped(org, InventoryMovement)
                .with_only_columns(InventoryMovement.id, InventoryMovement.movement_type)
                .where(InventoryMovement.id.in_(undone))
            ).tuples()
        }
    found.sort(key=lambda pair: (pair[0].occurred_at, pair[0].id))
    return [
        ItemMove(
            m.location_id,
            m.inventory_item_id,
            m.occurred_at,
            t.id,
            t.kind,
            t.source_type,
            original.get(m.reverses_movement_id, m.movement_type)
            if m.reverses_movement_id is not None
            else m.movement_type,
            Decimal(m.qty_base),
            Decimal(m.unit_cost_snapshot),
            m.reverses_movement_id is not None,
            t.source_id,
        )
        for m, t in found
    ]


def balances_before(
    db: Session, org: OrgContext, windows: Mapping[int, tuple[datetime, datetime]]
) -> dict[tuple[int, int], Decimal]:
    """What each item held at each location just before its window opened."""
    result: dict[tuple[int, int], Decimal] = {}
    for location_id, (start, _end) in windows.items():
        rows = db.execute(
            select_scoped(org, InventoryMovement)
            .with_only_columns(InventoryMovement.inventory_item_id, InventoryMovement.qty_base)
            .where(
                InventoryMovement.location_id == location_id,
                InventoryMovement.occurred_at < start,
            )
        ).tuples()
        for item_id, qty in rows:
            key = (location_id, item_id)
            result[key] = result.get(key, Decimal(0)) + Decimal(qty)
    return result


def counted_at(db: Session, org: OrgContext, item_ids: Iterable[int]) -> list[tuple[int, datetime]]:
    """When each count of these items moved the ledger, as (location id, instant): a count that
    matched the record leaves no movement and is not here."""
    ids = sorted(set(item_ids))
    if not ids:
        return []
    rows = db.execute(
        select_scoped(org, InventoryMovement)
        .with_only_columns(InventoryMovement.location_id, InventoryMovement.occurred_at)
        .where(
            InventoryMovement.inventory_item_id.in_(ids),
            InventoryMovement.movement_type == "count_variance",
            InventoryMovement.reverses_movement_id.is_(None),
        )
    ).tuples()
    return [(location_id, occurred_at) for location_id, occurred_at in rows]
