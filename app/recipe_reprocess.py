"""Applying a back-dated recipe version to past sales, only when the owner says so (C-51).

A product size's new version may start up to 30 days before it was saved. Saving it changes no
sale: what past sales took stays posted. This module previews, and on confirmation queues, the
reprocessing of the size's past sales that it now covers, each reversed and posted again at its
own moment (`reprocess_sale()`), within PD-17's bounds: from the last count of what the size
uses, and at most 30 days back. A sale whose line already read the version is left alone, so the
job is safe to repeat. Audited as `recipe.applied_to_past`.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.audit import record_audit
from app.consumption import reprocess_sale, resolve_variation, sale_moment
from app.jobs import enqueue
from app.mappings import business_today, last_count_for_size
from app.models import (
    InventoryItem,
    ProductVariation,
    RecipeVersion,
    SalesTransaction,
    SalesTransactionLine,
)
from app.recipes import FROM_THE_START, NotFound, Owner, effective_version, owner_name, recipe_of
from app.sales import lines_of_size
from app.transform.mappings import check_start, default_start, earliest_start
from app.transform.recipes import CLOCK_SKEW
from app.units import format_qty

log = logging.getLogger(__name__)
APPLY_JOB = "reprocess_recipe"


@dataclass(frozen=True)
class Preview:
    """What applying a size's back-dated version to its past sales from `start` would do."""

    variation: ProductVariation
    version: RecipeVersion
    start: date
    earliest: date
    today: date
    last_count: date | None
    sales: int
    units: Decimal
    uses: tuple[tuple[str, str], ...]  # (item, how much) those sales would take in all
    before_start: int  # sales the version covers from before `start`: they keep what they took


def back_dated(db: Session, org: OrgContext, variation: ProductVariation) -> RecipeVersion | None:
    """The size's version in effect now, when it was saved to start in the past."""
    recipe = recipe_of(db, org, Owner("variation", variation.id))
    version = effective_version(db, org, recipe) if recipe else None
    if (
        version is None
        or version.effective_from <= FROM_THE_START  # seeded history, not a back-dated edit
        or version.effective_from >= version.created_at - CLOCK_SKEW
    ):
        return None
    return version


def _waiting(
    db: Session, org: OrgContext, variation: ProductVariation, version: RecipeVersion, since: date
) -> list[tuple[SalesTransaction, SalesTransactionLine, datetime]]:
    """Past lines of the size that the version is in effect for but did not take stock by."""
    found = []
    for transaction, line in lines_of_size(db, org, variation.id, since):
        if line.recipe_version_id == version.id:
            continue
        moment = sale_moment(db, org, transaction)
        if moment < version.effective_from:
            continue
        used = resolve_variation(db, org, variation, Decimal(1), moment)
        if used.versions and used.versions[0] == version.id:
            found.append((transaction, line, moment))
    return found


def _size(db: Session, org: OrgContext, variation_id: int) -> ProductVariation:
    variation = get_scoped(db, org, ProductVariation, variation_id)
    if variation is None:
        raise NotFound(f"size {variation_id}")
    return variation


def preview(
    db: Session, org: OrgContext, variation_id: int, start: date | None = None
) -> Preview | None:
    """None when the size's current version was not back-dated."""
    variation = _size(db, org, variation_id)
    version = back_dated(db, org, variation)
    if version is None:
        return None
    now = datetime.now(UTC)
    today = business_today(db, org, now)
    last = last_count_for_size(db, org, variation, now)
    start = check_start(start or default_start(today, last), today)
    covered = _waiting(db, org, variation, version, earliest_start(today))
    chosen = [(t, ln, at) for t, ln, at in covered if t.business_date >= start]
    total: dict[int, Decimal] = defaultdict(Decimal)
    for _transaction, line, moment in chosen:
        used = resolve_variation(db, org, variation, Decimal(line.quantity), moment)
        for item_id, qty in used.uses.items():
            total[item_id] += qty
    items = {
        i.id: i
        for i in db.scalars(
            select_scoped(org, InventoryItem).where(InventoryItem.id.in_(list(total)))
        )
    }
    uses = tuple(
        (items[item].name, format_qty(qty, items[item].display_unit))
        for item, qty in sorted(total.items(), key=lambda kv: items[kv[0]].name.casefold())
        if item in items
    )
    return Preview(
        variation,
        version,
        start,
        earliest_start(today),
        today,
        last,
        len({t.id for t, _ln, _at in chosen}),
        sum((Decimal(ln.quantity) for _t, ln, _at in chosen), Decimal(0)),
        uses,
        len({t.id for t, _ln, _at in covered if t.business_date < start}),
    )


def queue_apply(
    db: Session, org: OrgContext, variation_id: int, start: date, *, ip: str | None = None
) -> Preview | None:
    """Confirm a preview: the sales it names are reprocessed in the background, audited."""
    shown = preview(db, org, variation_id, start)
    if shown is None or shown.sales == 0:
        return shown
    since = shown.start.isoformat()
    enqueue(
        db,
        org,
        APPLY_JOB,
        {"version_id": shown.version.id, "since": since},
        dedupe_key=f"recipe_version:{shown.version.id}:{since}",
    )
    name = owner_name(db, org, Owner("variation", shown.variation.id))
    record_audit(
        db,
        org,
        "recipe.applied_to_past",
        f"Applied version {shown.version.version_no} of the recipe for {name} to "
        f"{shown.sales} past sale(s) since {since}",
        subject_type="recipe",
        subject_id=shown.version.recipe_id,
        after={
            "version": shown.version.version_no,
            "since": since,
            "until": shown.today.isoformat(),
            "sales": shown.sales,
        },
        ip=ip,
    )
    return shown


def apply(db: Session, org: OrgContext, version_id: int, since: date) -> int:
    """The job: post the waiting sales again with the version; how many sales. A version no
    longer in effect now does nothing."""
    version = get_scoped(db, org, RecipeVersion, version_id)
    if version is None:
        return 0
    recipe = version.recipe
    if recipe.product_variation_id is None:
        return 0
    variation = _size(db, org, recipe.product_variation_id)
    current = back_dated(db, org, variation)
    if current is None or current.id != version.id:
        return 0
    reason = f"Recipe version {version.version_no} applied from {version.effective_from:%d %b %Y}"
    changed = sorted({t.id for t, _ln, _at in _waiting(db, org, variation, version, since)})
    for transaction_id in changed:
        reprocess_sale(db, org, transaction_id, reason=reason)
    log.info("applied recipe version %s to %d past sales", version.id, len(changed))
    return len(changed)
