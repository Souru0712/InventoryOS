"""Consumption: what each sale used, posted to the ledger (T2.05; WF-12, POS-FR-07, PRD-FR-11,
DR-18, ADR-0054).

A recorded sale's lines are resolved with the recipe versions in effect when it happened (a sale
with a time uses that instant; a day's sales entered as a whole use the end of that business day
at its location) and what they used leaves stock at the sale's own location, as one `sale`
transaction keyed `sale:<transaction id>:r<revision>`. It runs as a job after each sale and is
safe to run again. Only the latest revision of a sale, completed and counted, holds stock: a
correction or a void reverses what the one before it posted. What cannot be resolved is a gap on
the line and an exception, never a dropped sale.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.costs import cost_on
from app.exceptions import record_exception
from app.jobs import enqueue
from app.ledger.posting import (
    SYSTEM,
    post_transaction,
    posted_count,
    reverse_transaction,
    standing,
)
from app.models import (
    InventoryItem,
    ItemLocation,
    Location,
    Modifier,
    ProductVariation,
    RecipeVersion,
    SalesTransaction,
    SalesTransactionLine,
)
from app.products import scale_at
from app.recipes import Owner, effective_version, recipe_of
from app.transform.business_days import business_day_end
from app.transform.consumption import (
    ADD_WITHOUT_RECIPE,
    NO_RECIPE,
    NOT_MADE,
    UNMAPPED,
    LineIn,
    ModifierIn,
    RecipeIn,
    Resolved,
    resolve,
)
from app.transform.exceptions import Finding
from app.transform.ledger import Line, places
from app.transform.recipes import RECIPE_ITEM_TYPES

SOURCE = "sales_transaction"


def sale_moment(db: Session, org: OrgContext, transaction: SalesTransaction) -> datetime:
    """When a sale happened, for choosing recipe versions: its time, or the end of its business
    day at its location for a day's sales entered as a whole."""
    if transaction.occurred_at is not None:
        return transaction.occurred_at
    place = get_scoped(db, org, Location, transaction.location_id)
    zone = place.timezone if place else "UTC"
    cutoff = place.business_day_cutoff_hour if place else 0
    return business_day_end(transaction.business_date, zone, cutoff)


def _recipe_in(version: RecipeVersion | None) -> RecipeIn | None:
    if version is None:
        return None
    return RecipeIn(
        version.id,
        tuple(
            (ln.inventory_item_id, Decimal(ln.qty_base), ln.is_packaging) for ln in version.lines
        ),
        Decimal(version.yield_base),
    )


def _version_of(db: Session, org: OrgContext, owner: Owner, at: datetime) -> RecipeIn | None:
    recipe = recipe_of(db, org, owner)
    return _recipe_in(effective_version(db, org, recipe, at)) if recipe else None


def made_in_house(db: Session, org: OrgContext) -> frozenset[int]:
    """The business's prepared and finished items: made from recipes of their own."""
    return frozenset(
        item.id
        for item in db.scalars(
            select_scoped(org, InventoryItem).where(InventoryItem.item_type.in_(RECIPE_ITEM_TYPES))
        )
    )


def resolve_line(
    db: Session,
    org: OrgContext,
    line: SalesTransactionLine,
    at: datetime,
    in_house: frozenset[int] | None = None,
) -> Resolved:
    """What one sold line used, by the recipes in effect at `at` (WF-12 steps 1 to 5)."""
    variation = (
        get_scoped(db, org, ProductVariation, line.product_variation_id)
        if line.product_variation_id is not None
        else None
    )
    if variation is None:
        return resolve(LineIn(Decimal(line.quantity), None, mapped=False))
    modifiers = []
    for chosen in line.modifiers:
        modifier = get_scoped(db, org, Modifier, chosen.modifier_id)
        if modifier is None:
            continue
        modifiers.append(
            ModifierIn(
                modifier.id,
                modifier.effect,
                scale_at(db, org, modifier, at),
                _version_of(db, org, Owner("modifier", modifier.id), at)
                if modifier.effect == "add"
                else None,
                chosen.quantity,
            )
        )
    return resolve(
        LineIn(
            Decimal(line.quantity),
            _version_of(db, org, Owner("variation", variation.id), at),
            made_to_order=variation.product.production_mode == "made_to_order",
            modifiers=tuple(modifiers),
        ),
        prepared=lambda item_id: _version_of(db, org, Owner("item", item_id), at),
        prepared_ids=in_house if in_house is not None else made_in_house(db, org),
    )


def resolve_variation(
    db: Session, org: OrgContext, variation: ProductVariation, quantity: Decimal, at: datetime
) -> Resolved:
    """What that many of a product size uses by the recipes in effect at `at`, with no
    modifiers: how waste of a sellable product expands (WST-FR-01, AC-WST-04)."""
    return resolve(
        LineIn(
            quantity,
            _version_of(db, org, Owner("variation", variation.id), at),
            made_to_order=variation.product.production_mode == "made_to_order",
        ),
        prepared=lambda item_id: _version_of(db, org, Owner("item", item_id), at),
        prepared_ids=made_in_house(db, org),
    )


def resolve_sale(
    db: Session, org: OrgContext, transaction: SalesTransaction
) -> dict[int, Resolved]:
    """Every line of a sale, resolved at the sale's moment: line id -> what it used."""
    at = sale_moment(db, org, transaction)
    in_house = made_in_house(db, org)
    return {line.id: resolve_line(db, org, line, at, in_house) for line in transaction.lines}


# --- posting (WF-12 step 7) ----------------------------------------------------------------------


def _counts(transaction: SalesTransaction) -> bool:
    return transaction.status == "completed" and transaction.of_record


def _revisions(db: Session, org: OrgContext, transaction: SalesTransaction):
    return list(
        db.scalars(
            select_scoped(org, SalesTransaction).where(
                SalesTransaction.sales_source_id == transaction.sales_source_id,
                SalesTransaction.source_reference == transaction.source_reference,
            )
        )
    )


def _undo(db: Session, org: OrgContext, transaction: SalesTransaction, reason: str) -> int:
    """Reverse what a sale still holds in stock, at the moment it was posted."""
    undone = 0
    for posting in standing(db, org, SOURCE, str(transaction.id)):
        reverse_transaction(
            db,
            org,
            posting.id,
            reason=reason,
            occurred_at=posting.occurred_at,
            actor_user_id=SYSTEM,
        )
        undone += 1
    return undone


def _names(db: Session, org: OrgContext, ids: set[int]) -> dict[int, str]:
    rows = select_scoped(org, InventoryItem).where(InventoryItem.id.in_(ids))
    return {item.id: item.name for item in db.scalars(rows)}


def _gaps(
    db: Session,
    org: OrgContext,
    place: Location,
    line: SalesTransactionLine,
    used: Resolved,
    in_house: frozenset[int],
    at: datetime,
    unstocked: list[int],
) -> None:
    """Each gap is an `unmapped_product` exception on what is missing (DOMAIN_MODEL §6), so a
    person can map, write the recipe or stock the item, then reprocess."""
    findings: list[Finding] = []
    variation = (
        get_scoped(db, org, ProductVariation, line.product_variation_id)
        if line.product_variation_id is not None
        else None
    )
    sold = f"{variation.product.name} {variation.name}" if variation else line.description
    if UNMAPPED in used.gaps:
        findings.append(
            Finding(
                type="unmapped_product",
                subject_type="pos_item",
                subject_id=f"{place.id}:{line.external_ref or line.line_key}"[:80],
                title=(
                    f"{sold or line.external_ref} sold at {place.name} is not linked to a "
                    "product, so its sales use nothing from stock yet."
                ),
                location_id=place.id,
            )
        )
    if NO_RECIPE in used.gaps and variation is not None:
        findings.append(
            Finding(
                type="unmapped_product",
                subject_type="product_variation",
                subject_id=str(variation.id),
                title=f"{sold} was sold with no recipe in effect, so it used nothing from stock.",
            )
        )
    if ADD_WITHOUT_RECIPE in used.gaps:
        for chosen in line.modifiers:
            modifier = get_scoped(db, org, Modifier, chosen.modifier_id)
            if modifier is None or modifier.effect != "add":
                continue
            if _version_of(db, org, Owner("modifier", modifier.id), at) is None:
                findings.append(
                    Finding(
                        type="unmapped_product",
                        subject_type="modifier",
                        subject_id=str(modifier.id),
                        title=(
                            f"{modifier.name} adds to what is sold but has no recipe, so it "
                            "used nothing from stock."
                        ),
                    )
                )
    unmade = [item for item in used.uses if item in in_house] if NOT_MADE in used.gaps else []
    names = _names(db, org, {*unmade, *unstocked})
    for item in unmade:
        findings.append(
            Finding(
                type="unmapped_product",
                subject_type="item",
                subject_id=str(item),
                title=(
                    f"{names.get(item, 'A prepared item')} is made to order in {sold} but has no "
                    "recipe, so the sale used it as it is."
                ),
            )
        )
    for item in unstocked:
        findings.append(
            Finding(
                type="unmapped_product",
                subject_type="item_location",
                subject_id=f"{item}:{place.id}",
                title=(
                    f"{names.get(item, 'An item')} is used by {sold} but is not stocked at "
                    f"{place.name}, so that use was not recorded."
                ),
                location_id=place.id,
            )
        )
    for finding in findings:
        record_exception(db, org, finding)


def _post(db: Session, org: OrgContext, transaction: SalesTransaction) -> bool:
    """Resolve a counted sale's pending lines and post what they used; False when there was
    nothing pending (it was done before)."""
    pending = [ln for ln in transaction.lines if ln.consumption_status == "pending"]
    if not pending:
        return False
    place = get_scoped(db, org, Location, transaction.location_id)
    if place is None:
        raise LookupError(f"location {transaction.location_id} not found")
    at = sale_moment(db, org, transaction)
    in_house = made_in_house(db, org)
    stocked = {
        row.inventory_item_id
        for row in db.scalars(
            select_scoped(org, ItemLocation).where(ItemLocation.location_id == place.id)
        )
    }
    totals: dict[int, Decimal] = defaultdict(Decimal)
    for line in pending:
        used = resolve_line(db, org, line, at, in_house)
        unstocked = sorted(item for item in used.uses if item not in stocked)
        for item, qty in used.uses.items():
            if item in stocked:
                totals[item] += qty
        line.recipe_version_id = used.versions[0] if used.versions else None
        line.consumption_status = "gap" if used.gaps or unstocked else "posted"
        _gaps(db, org, place, line, used, in_house, at, unstocked)
    lines = []
    uncosted = []
    for item, qty in sorted(totals.items()):
        amount = places(qty)
        if amount == 0:
            continue
        cost = cost_on(db, org, place.id, item, at)
        if not cost:
            uncosted.append(item)
        lines.append(
            Line(
                location_id=place.id,
                inventory_item_id=item,
                movement_type="sale_consumption",
                qty_base=-amount,
                unit_cost=cost if cost else None,
            )
        )
    if lines:
        done = posted_count(db, org, SOURCE, str(transaction.id))
        key = f"sale:{transaction.id}:r{transaction.revision}" + (f":p{done + 1}" if done else "")
        post_transaction(
            db,
            org,
            kind="sale",
            source_type=SOURCE,
            source_id=str(transaction.id),
            idempotency_key=key,
            lines=lines,
            occurred_at=at,
            actor_user_id=SYSTEM,
        )
        names = _names(db, org, set(uncosted))
        for item in uncosted:
            record_exception(
                db,
                org,
                Finding(
                    type="missing_cost",
                    subject_type="item_location",
                    subject_id=f"{item}:{place.id}",
                    title=(
                        f"{names.get(item, 'An item')} was used by sales at {place.name} with "
                        "no cost, so that use is valued at zero."
                    ),
                    location_id=place.id,
                ),
            )
        # Stock just went down: check it for low stock and below zero (WF-12 notifications).
        enqueue(db, org, "evaluate_exceptions", dedupe_key="evaluate")
    return True


def consume_sale(
    db: Session, org: OrgContext, transaction_id: int, reason: str | None = None
) -> bool:
    """WF-12 for one recorded sale; safe to run again. Earlier revisions give back what they
    posted, with the person's reason when a correction gave one (STD-FR-05); the latest, if it
    is completed and counted, posts what its lines used. True when something was posted."""
    correction = reason
    transaction = get_scoped(db, org, SalesTransaction, transaction_id)
    if transaction is None:
        return False
    revisions = _revisions(db, org, transaction)
    latest = max(revisions, key=lambda t: t.revision)
    for revision in revisions:
        if revision is latest and _counts(revision):
            continue
        if revision is not latest:
            reason = (
                f"Sale corrected: {why}"
                if (why := (correction or "").strip())
                else f"Sale corrected: revision {latest.revision} replaces it"
            )
        elif revision.status == "voided":
            reason = "Sale voided"
        else:
            reason = "Sale not counted for its day"
        _undo(db, org, revision, reason)
        for line in revision.lines:
            if line.consumption_status == "pending":
                line.consumption_status = "skipped"
    if not _counts(latest):
        db.flush()
        return False
    posted = _post(db, org, latest)
    db.flush()
    return posted


def reprocess_sale(db: Session, org: OrgContext, transaction_id: int, *, reason: str) -> bool:
    """Resolve a sale again with today's mappings and recipes (WF-28): what it posted is
    reversed and its lines are posted afresh, at the sale's own moment. True when something
    was posted."""
    transaction = get_scoped(db, org, SalesTransaction, transaction_id)
    if transaction is None or not _counts(transaction):
        return False
    _undo(db, org, transaction, reason)
    for line in transaction.lines:
        line.consumption_status = "pending"
    return consume_sale(db, org, transaction.id)


def queue_consumption(
    db: Session, org: OrgContext, transaction: SalesTransaction, reason: str | None = None
) -> None:
    """Consume a sale in the background: a failure leaves its lines pending and is retried
    (WF-12), and never holds up recording it."""
    payload: dict = {"sales_transaction_id": transaction.id}
    if reason:
        payload["reason"] = reason[:200]
    enqueue(
        db,
        org,
        "consume_sale",
        payload,
        dedupe_key=f"sale:{transaction.id}",
    )
