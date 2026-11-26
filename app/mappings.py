"""POS product mapping and reprocessing (T3.04, ADR-0078; POS-FR-06, POS-FR-14, PD-17, WF-28).

The only writer of `product_mappings`. A POS variation maps onto one of the business's product
sizes and a POS modifier onto one of its modifiers. A change supersedes the active mapping, so its
history stays, and is audited. Mapping a POS item clears its open `unmapped_product` exceptions.

Its past sales are not changed by mapping alone. Reprocessing (a job, after a preview) points the
counted lines since a start date at the mapped size, then reverses and posts each of those sales
again at its own moment (WF-12). Repeating it finds nothing left to change. The default start is
the last count of what the size uses, and never more than 30 days back (PD-17).
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
from app.exceptions import clear
from app.jobs import enqueue
from app.ledger.history import counted_at
from app.models import (
    ExceptionRecord,
    ExternalModifier,
    ExternalProduct,
    InventoryItem,
    Location,
    Modifier,
    Product,
    ProductMapping,
    ProductVariation,
    ProviderConnection,
    StockCount,
    StockCountLine,
)
from app.products import NewVariation, add_variation, create_product, list_products
from app.sales import lines_to_remap, remap_lines, unmapped_units
from app.transform.business_days import business_date
from app.transform.exceptions import OPEN_STATUSES
from app.transform.mappings import (
    ACTIVE,
    SUPERSEDED,
    Candidate,
    MappingRuleError,
    check_start,
    default_start,
    earliest_start,
    suggest,
)
from app.units import format_qty

log = logging.getLogger(__name__)

REPROCESS_JOB = "reprocess_mapping"
REGULAR = "Regular"

__all__ = ["MappingRuleError", "NotFound", "NotMapped"]


class NotFound(LookupError):
    """No such POS product, modifier, size or modifier in this business."""


class NotMapped(MappingRuleError):
    """Reprocessing asked for a POS product that maps to nothing."""


def label(external: ExternalProduct) -> str:
    return " ".join(p for p in (external.name, external.variation_name) if p) or (
        external.external_variation_id
    )


def size_label(variation: ProductVariation) -> str:
    return f"{variation.product.name} {variation.name}"


@dataclass(frozen=True)
class ProductRow:
    """A POS product on the mapping screen: what it maps to, what sold unmapped, suggestions."""

    external: ExternalProduct
    mapped: ProductVariation | None
    unmapped_units: Decimal
    suggestions: tuple[int, ...]


def _external(db: Session, org: OrgContext, external_product_id: int) -> ExternalProduct:
    external = get_scoped(db, org, ExternalProduct, external_product_id)
    if external is None:
        raise NotFound("no such POS product")
    return external


def _provider(db: Session, org: OrgContext, connection_id: int) -> str:
    connection = get_scoped(db, org, ProviderConnection, connection_id)
    if connection is None:
        raise NotFound("no such connection")
    return connection.provider


def active_mapping(
    db: Session, org: OrgContext, *, product: int | None = None, modifier: int | None = None
) -> ProductMapping | None:
    query = select_scoped(org, ProductMapping).where(ProductMapping.status == ACTIVE)
    if product is not None:
        query = query.where(ProductMapping.external_product_id == product)
    else:
        query = query.where(ProductMapping.external_modifier_id == modifier)
    return db.scalars(query).first()


def sizes(db: Session, org: OrgContext) -> list[ProductVariation]:
    """Every size still sold, by product then size: what a POS product can map to."""
    return sorted(
        (
            v
            for product in list_products(db, org)
            for v in product.variations
            if v.is_active and product.is_active
        ),
        key=lambda v: (v.product.name.casefold(), v.name.casefold()),
    )


def product_rows(db: Session, org: OrgContext, connection: ProviderConnection) -> list[ProductRow]:
    """The connection's live POS products, those sold without a size first (most sold first)."""
    externals = list(
        db.scalars(
            select_scoped(org, ExternalProduct).where(
                ExternalProduct.connection_id == connection.id, ExternalProduct.active.is_(True)
            )
        )
    )
    mappings = {
        m.external_product_id: m.product_variation_id
        for m in db.scalars(
            select_scoped(org, ProductMapping).where(
                ProductMapping.status == ACTIVE, ProductMapping.external_product_id.is_not(None)
            )
        )
    }
    choices = sizes(db, org)
    by_id = {v.id: v for v in choices}
    candidates = [Candidate(v.id, v.product.name, v.name) for v in choices]
    sold = unmapped_units(db, org, connection.provider)
    rows = []
    for external in externals:
        mapped_id = mappings.get(external.id)
        mapped = by_id.get(mapped_id) if mapped_id else None
        if mapped is None and mapped_id is not None:
            mapped = get_scoped(db, org, ProductVariation, mapped_id)  # no longer sold
        rows.append(
            ProductRow(
                external,
                mapped,
                sold.get(external.external_variation_id, Decimal(0)),
                ()
                if mapped is not None
                else tuple(suggest(external.name, external.variation_name, candidates)),
            )
        )
    return sorted(
        rows,
        key=lambda r: (r.mapped is not None, -r.unmapped_units, label(r.external).casefold()),
    )


def modifier_rows(
    db: Session, org: OrgContext, connection: ProviderConnection
) -> list[tuple[ExternalModifier, Modifier | None]]:
    externals = list(
        db.scalars(
            select_scoped(org, ExternalModifier)
            .where(
                ExternalModifier.connection_id == connection.id, ExternalModifier.active.is_(True)
            )
            .order_by(ExternalModifier.list_name, ExternalModifier.name)
        )
    )
    rows = []
    for external in externals:
        mapping = active_mapping(db, org, modifier=external.id)
        target = (
            get_scoped(db, org, Modifier, mapping.modifier_id)
            if mapping and mapping.modifier_id
            else None
        )
        rows.append((external, target))
    return rows


def unmapped_sales(db: Session, org: OrgContext, external_product_id: int) -> Decimal:
    """How many of the POS product sold with no size: what reprocessing would pick up."""
    external = _external(db, org, external_product_id)
    sold = unmapped_units(db, org, _provider(db, org, external.connection_id))
    return sold.get(external.external_variation_id, Decimal(0))


def _supersede(db: Session, current: ProductMapping | None, now: datetime) -> None:
    if current is not None:
        current.status = SUPERSEDED
        current.superseded_at = now
        db.flush()  # the one-active index must be free before the next row


def _clear_unmapped(db: Session, org: OrgContext, external_ref: str, now: datetime) -> None:
    """The open exceptions consumption raised for this POS item, at every location."""
    open_ones = db.scalars(
        select_scoped(org, ExceptionRecord).where(
            ExceptionRecord.type == "unmapped_product",
            ExceptionRecord.subject_type == "pos_item",
            ExceptionRecord.status.in_(OPEN_STATUSES),
        )
    )
    for row in list(open_ones):
        if row.subject_id == f"{row.location_id}:{external_ref}"[:80]:
            clear(db, org, (row.type, row.subject_type, row.subject_id), now)


def map_product(
    db: Session,
    org: OrgContext,
    external_product_id: int,
    variation_id: int | None,
    *,
    ip: str | None = None,
) -> bool:
    """Map a POS variation to a product size, or to none; False when nothing changed."""
    external = _external(db, org, external_product_id)
    variation = None
    if variation_id is not None:
        variation = get_scoped(db, org, ProductVariation, variation_id)
        if variation is None:
            raise NotFound("no such size")
        if not (variation.is_active and variation.product.is_active):
            raise MappingRuleError(f"{size_label(variation)} is no longer sold.")
    current = active_mapping(db, org, product=external.id)
    before = current.product_variation_id if current else None
    if before == variation_id:
        return False
    now = datetime.now(UTC)
    old = get_scoped(db, org, ProductVariation, before) if before else None
    _supersede(db, current, now)
    if variation is not None:
        db.add(
            ProductMapping(
                tenant_id=org.tenant_id,
                external_product_id=external.id,
                product_variation_id=variation.id,
                mapped_by_user_id=org.user_id,
                mapped_at=now,
            )
        )
        db.flush()
        _clear_unmapped(db, org, external.external_variation_id, now)
    record_audit(
        db,
        org,
        "mapping.product",
        f"Mapped {label(external)} to {size_label(variation)}"
        if variation
        else f"Unmapped {label(external)}",
        subject_type="external_product",
        subject_id=external.id,
        before={"mapped_to": size_label(old) if old else None},
        after={"mapped_to": size_label(variation) if variation else None},
        ip=ip,
    )
    return True


def create_and_map(
    db: Session, org: OrgContext, external_product_id: int, *, ip: str | None = None
) -> ProductVariation:
    """Make the POS product the business's own and map it (WF-28 step 2): its size added to the
    product of the same name, or a new product with that size."""
    external = _external(db, org, external_product_id)
    size = external.variation_name.strip() or REGULAR
    product: Product | None = next(
        (
            p
            for p in list_products(db, org, inactive=True)
            if p.name.casefold() == external.name.strip().casefold()
        ),
        None,
    )
    if product is None:
        product = create_product(
            db,
            org,
            name=external.name,
            category=external.category or "",
            variations=[NewVariation(size)],
            ip=ip,
        )
        variation = product.variations[0]
    else:
        variation = next(
            (v for v in product.variations if v.name.casefold() == size.casefold()), None
        ) or add_variation(db, org, product.id, NewVariation(size), ip=ip)
    map_product(db, org, external.id, variation.id, ip=ip)
    return variation


def map_modifier(
    db: Session,
    org: OrgContext,
    external_modifier_id: int,
    modifier_id: int | None,
    *,
    ip: str | None = None,
) -> bool:
    """Map a POS modifier to one of the business's modifiers, or to none."""
    external = get_scoped(db, org, ExternalModifier, external_modifier_id)
    if external is None:
        raise NotFound("no such POS modifier")
    modifier = None
    if modifier_id is not None:
        modifier = get_scoped(db, org, Modifier, modifier_id)
        if modifier is None:
            raise NotFound("no such modifier")
    current = active_mapping(db, org, modifier=external.id)
    before = current.modifier_id if current else None
    if before == modifier_id:
        return False
    now = datetime.now(UTC)
    old = get_scoped(db, org, Modifier, before) if before else None
    _supersede(db, current, now)
    if modifier is not None:
        db.add(
            ProductMapping(
                tenant_id=org.tenant_id,
                external_modifier_id=external.id,
                modifier_id=modifier.id,
                mapped_by_user_id=org.user_id,
                mapped_at=now,
            )
        )
        db.flush()
        # Order lines raised it while unmapped (T3.05); mapping is the fix.
        clear(
            db, org, ("unmapped_product", "pos_modifier", external.external_modifier_id[:80]), now
        )
    name = f"{external.list_name}: {external.name}" if external.list_name else external.name
    record_audit(
        db,
        org,
        "mapping.modifier",
        f"Mapped {name} to {modifier.name}" if modifier else f"Unmapped {name}",
        subject_type="external_modifier",
        subject_id=external.id,
        before={"mapped_to": old.name if old else None},
        after={"mapped_to": modifier.name if modifier else None},
        ip=ip,
    )
    return True


# --- reprocessing (POS-FR-14, PD-17) -------------------------------------------------------------


@dataclass(frozen=True)
class Preview:
    """What reprocessing a mapping from `start` would do, before anyone confirms it."""

    external: ExternalProduct
    variation: ProductVariation
    start: date
    earliest: date
    today: date
    last_count: date | None
    sales: int
    units: Decimal
    uses: tuple[tuple[str, str], ...]  # (item, how much), what those sales would take from stock
    incomplete: bool  # the size's recipe leaves something out (no recipe, an unmade item)
    before_recipe: int = 0  # sales from before the size had a recipe: they still use nothing


def business_today(db: Session, org: OrgContext, now: datetime) -> date:
    places = list(db.scalars(select_scoped(org, Location).where(Location.is_active.is_(True))))
    days = [business_date(now, p.timezone, p.business_day_cutoff_hour) for p in places]
    return max(days) if days else now.date()


def last_count_for_size(
    db: Session, org: OrgContext, variation: ProductVariation, now: datetime
) -> date | None:
    """The business date of the latest count of anything the size uses, at its location."""
    items = set(resolve_variation(db, org, variation, Decimal(1), now).uses)
    if not items:
        return None
    instants = counted_at(db, org, items)
    instants += [
        (location_id, line.counted_at)
        for line, location_id in db.execute(
            select_scoped(org, StockCountLine)
            .add_columns(StockCount.location_id)
            .join(StockCount, StockCount.id == StockCountLine.stock_count_id)
            .where(StockCountLine.inventory_item_id.in_(items), StockCount.status == "posted")
        ).tuples()
    ]
    places = {p.id: p for p in db.scalars(select_scoped(org, Location))}
    days = [
        business_date(at, places[loc].timezone, places[loc].business_day_cutoff_hour)
        for loc, at in instants
        if loc in places
    ]
    return max(days) if days else None


def preview(
    db: Session, org: OrgContext, external_product_id: int, start: date | None = None
) -> Preview:
    external = _external(db, org, external_product_id)
    mapping = active_mapping(db, org, product=external.id)
    if mapping is None or mapping.product_variation_id is None:
        raise NotMapped(f"Map {label(external)} before reprocessing its sales.")
    variation = get_scoped(db, org, ProductVariation, mapping.product_variation_id)
    if variation is None:
        raise NotFound("no such size")
    now = datetime.now(UTC)
    today = business_today(db, org, now)
    last = last_count_for_size(db, org, variation, now)
    start = check_start(start or default_start(today, last), today)
    pairs = lines_to_remap(
        db,
        org,
        _provider(db, org, external.connection_id),
        external.external_variation_id,
        variation.id,
        start,
    )
    units = sum((Decimal(line.quantity) for _, line in pairs), Decimal(0))
    # Each sale reads the recipe in effect at its own moment, as the job will (DR-16): a recipe
    # saved after a sale gives that sale nothing.
    total: dict[int, Decimal] = defaultdict(Decimal)
    incomplete = False
    before_recipe: set[int] = set()
    for transaction, line in pairs:
        moment = sale_moment(db, org, transaction)
        used = resolve_variation(db, org, variation, Decimal(line.quantity), moment)
        for item_id, qty in used.uses.items():
            total[item_id] += qty
        incomplete = incomplete or bool(used.gaps)
        if not used.versions:
            before_recipe.add(transaction.id)
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
        external,
        variation,
        start,
        earliest_start(today),
        today,
        last,
        len({transaction.id for transaction, _ in pairs}),
        units,
        uses,
        incomplete and units > 0,
        len(before_recipe),
    )


def queue_reprocess(
    db: Session, org: OrgContext, external_product_id: int, start: date, *, ip: str | None = None
) -> Preview:
    """Confirm a preview: the sales it names are reprocessed in the background, audited with the
    range (WF-28)."""
    shown = preview(db, org, external_product_id, start)
    if shown.sales == 0:
        return shown
    mapping = active_mapping(db, org, product=shown.external.id)
    if mapping is None:  # unmapped since the preview was read
        raise NotMapped(f"Map {label(shown.external)} before reprocessing its sales.")
    since = shown.start.isoformat()
    enqueue(
        db,
        org,
        REPROCESS_JOB,
        {"mapping_id": mapping.id, "since": since},
        dedupe_key=f"mapping:{mapping.id}:{since}",
    )
    record_audit(
        db,
        org,
        "mapping.reprocessed",
        f"Reprocessed {shown.sales} sale(s) of {label(shown.external)} since {since}",
        subject_type="external_product",
        subject_id=shown.external.id,
        after={"since": since, "until": shown.today.isoformat(), "sales": shown.sales},
        ip=ip,
    )
    return shown


def reprocess(db: Session, org: OrgContext, mapping_id: int, since: date) -> int:
    """The job: point the lines at the mapped size and post their sales again; how many sales.
    A mapping superseded since then does nothing (its successor reprocesses for itself)."""
    mapping = get_scoped(db, org, ProductMapping, mapping_id)
    if (
        mapping is None
        or mapping.status != ACTIVE
        or mapping.product_variation_id is None
        or mapping.external_product_id is None
    ):
        return 0
    external = get_scoped(db, org, ExternalProduct, mapping.external_product_id)
    variation = get_scoped(db, org, ProductVariation, mapping.product_variation_id)
    if external is None or variation is None:
        return 0
    changed = remap_lines(
        db,
        org,
        _provider(db, org, external.connection_id),
        external.external_variation_id,
        variation.id,
        since,
    )
    reason = f"Mapped {label(external)} to {size_label(variation)}"
    for transaction_id in changed:
        reprocess_sale(db, org, transaction_id, reason=reason)
    log.info("reprocessed %d sales for mapping %s", len(changed), mapping.id)
    return len(changed)
