"""Sales file import (T2.13; STD-FR-03, AC-SAL-04, ADR-0064).

The only writer of `sales_imports`, `import_mappings` and `import_names`. An export from a card
terminal, register or delivery platform is uploaded (kept in the business's storage), its columns
mapped once per header and remembered, its product names matched (learned names first, then each
size's own label) and previewed: what each day sold, the names still unmatched, and the days
whose sales come from another source. Posting records one `file_import` daily transaction per day
(reference `import:<date>`) through `record_sale()`; the same file again records nothing, so it
never consumes twice (AC-SAL-04). CSV for now; XLSX waits for its reader (ID-23).
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.config import get_settings
from app.consumption import consume_sale
from app.models import (
    ImportMapping,
    ImportName,
    Location,
    ProductVariation,
    SalesImport,
    SalesSource,
)
from app.sales import NotFound, add_source, extend_source, record_sale, source_of_record, sources
from app.storage import get_storage
from app.transform.imports import (
    ColumnMap,
    ImportRuleError,
    guess_columns,
    match_names,
    normalized,
    read_csv,
    sold_by_day,
)
from app.transform.sales import SaleLine

log = logging.getLogger(__name__)


def upload(
    db: Session,
    org: OrgContext,
    *,
    location_id: int,
    filename: str,
    content: bytes,
    uploaded_by: int | None = None,
) -> SalesImport:
    """Keep an uploaded sales file in the business's storage (STD-FR-03). Only text CSV is
    read; its type comes from its bytes."""
    place = get_scoped(db, org, Location, location_id)
    if place is None:
        raise NotFound(location_id)
    if len(content) > get_settings().max_upload_mb * 1024 * 1024:
        raise ImportRuleError(f"Files are at most {get_settings().max_upload_mb} MB.")
    if content[:4] == b"PK\x03\x04":
        raise ImportRuleError("Excel files are not read yet: save it as CSV and upload that.")
    if b"\x00" in content[:4096]:
        raise ImportRuleError("That is not a CSV file.")
    read_csv(content)  # refuses an empty or oversized file now, not at preview
    digest = hashlib.sha256(content).hexdigest()
    key = f"{org.tenant_id}/imports/{digest}.csv"
    get_storage().put(key, content, "text/csv")
    row = SalesImport(
        tenant_id=org.tenant_id,
        location_id=place.id,
        filename=(filename or "sales.csv")[:200],
        sha256=digest,
        storage_key=key,
        uploaded_by=uploaded_by,
    )
    db.add(row)
    db.flush()
    return row


def get_import(db: Session, org: OrgContext, import_id: int) -> SalesImport:
    row = get_scoped(db, org, SalesImport, import_id)
    if row is None:
        raise NotFound(import_id)
    return row


def table(row: SalesImport) -> tuple[list[str], list[list[str]]]:
    return read_csv(get_storage().get(row.storage_key))


def _header_key(header: list[str]) -> str:
    return "|".join(normalized(h) for h in header)[:500]


def mapping_for(db: Session, org: OrgContext, row: SalesImport) -> ColumnMap | None:
    """The columns chosen for this file: its own, a saved one for the same header, or a guess."""
    header, _rows = table(row)
    saved = (
        get_scoped(db, org, ImportMapping, row.mapping_id)
        if row.mapping_id
        else db.scalars(
            select_scoped(org, ImportMapping).where(ImportMapping.header_key == _header_key(header))
        ).first()
    )
    if saved is not None:
        return ColumnMap(
            saved.date_column, saved.product_column, saved.quantity_column, saved.date_format
        )
    return guess_columns(header)


def save_mapping(db: Session, org: OrgContext, import_id: int, columns: ColumnMap) -> ImportMapping:
    """Remember which columns hold what, for every file with this header."""
    row = get_import(db, org, import_id)
    header, rows = table(row)
    sold_by_day(header, rows[:50], columns)  # refuses a mapping that does not read
    key = _header_key(header)
    saved = db.scalars(
        select_scoped(org, ImportMapping).where(ImportMapping.header_key == key)
    ).first()
    if saved is None:
        saved = ImportMapping(tenant_id=org.tenant_id, header_key=key)
        db.add(saved)
    saved.date_column = columns.date_column
    saved.product_column = columns.product_column
    saved.quantity_column = columns.quantity_column
    saved.date_format = columns.date_format
    db.flush()
    row.mapping_id = saved.id
    db.flush()
    return saved


def teach(db: Session, org: OrgContext, name: str, variation_id: int) -> ImportName:
    """Match a name from the files to a product size, for this and every later import."""
    if get_scoped(db, org, ProductVariation, variation_id) is None:
        raise NotFound(variation_id)
    key = normalized(name)
    found = db.scalars(select_scoped(org, ImportName).where(ImportName.name == key)).first()
    if found is None:
        found = ImportName(tenant_id=org.tenant_id, name=key[:300])
        db.add(found)
    found.product_variation_id = variation_id
    db.flush()
    return found


def _labels(db: Session, org: OrgContext) -> dict[str, int]:
    sizes = [
        v
        for v in db.scalars(select_scoped(org, ProductVariation))
        if v.is_active and v.product.is_active
    ]
    by_product: dict[int, list[ProductVariation]] = defaultdict(list)
    for v in sizes:
        by_product[v.product_id].append(v)
    labels = {normalized(f"{v.product.name} {v.name}"): v.id for v in sizes}
    for group in by_product.values():
        if len(group) == 1:
            labels.setdefault(normalized(group[0].product.name), group[0].id)
    return labels


@dataclass
class Preview:
    """What posting would record (AC-SAL-04)."""

    days: dict[date, dict[int, Decimal]] = field(default_factory=dict)
    unmatched: dict[str, Decimal] = field(default_factory=dict)
    blocked: dict[date, str] = field(default_factory=dict)  # day -> where its sales come from
    rows: int = 0


def preview(db: Session, org: OrgContext, import_id: int) -> Preview:
    row = get_import(db, org, import_id)
    columns = mapping_for(db, org, row)
    if columns is None:
        raise ImportRuleError("Choose which columns hold the date, product and quantity.")
    header, rows = table(row)
    totals = sold_by_day(header, rows, columns)
    learned = {n.name: n.product_variation_id for n in db.scalars(select_scoped(org, ImportName))}
    matched, unmatched_names = match_names(
        (name for _day, name in totals), learned, _labels(db, org)
    )
    result = Preview(rows=len(rows))
    for (day, name), qty in totals.items():
        if name in matched:
            day_lines = result.days.setdefault(day, {})
            day_lines[matched[name]] = day_lines.get(matched[name], Decimal(0)) + qty
        else:
            result.unmatched[name] = result.unmatched.get(name, Decimal(0)) + qty
    for day in result.days:
        current = source_of_record(db, org, row.location_id, day)
        if current is not None and current.kind != "file_import":
            result.blocked[day] = current.kind
    result.days = dict(sorted(result.days.items()))
    return result


def _import_source(db: Session, org: OrgContext, location_id: int, day: date) -> SalesSource:
    current = source_of_record(db, org, location_id, day)
    if current is not None:
        return current
    later = [s for s in sources(db, org, location_id) if s.of_record and s.active_from > day]
    following = min(later, key=lambda s: s.active_from, default=None)
    if following is not None and following.kind == "file_import":
        return extend_source(db, org, following.id, day)
    # the new source stops before the next source of record so the two never overlap (DR-31)
    until = following.active_from - timedelta(days=1) if following is not None else None
    return add_source(
        db, org, location_id=location_id, kind="file_import", active_from=day, active_to=until
    )


def post(db: Session, org: OrgContext, import_id: int) -> int:
    """Record each previewed day that is not another source's (AC-SAL-04); the number of days
    recorded or changed. Importing the same file again records nothing."""
    row = get_import(db, org, import_id)
    shown = preview(db, org, import_id)
    written = 0
    for day, lines in shown.days.items():
        if day in shown.blocked or not lines:
            continue
        source = _import_source(db, org, row.location_id, day)
        sale = record_sale(
            db,
            org,
            source,
            source_reference=f"import:{day.isoformat()}",
            lines=[SaleLine(f"v:{vid}", qty, vid) for vid, qty in sorted(lines.items()) if qty],
            day=day,
            reason=f"Re-imported from {row.filename}",
        )
        if sale is None:
            continue
        written += 1
        try:
            with db.begin_nested():
                consume_sale(db, org, sale.id, f"Re-imported from {row.filename}")
        except Exception:  # noqa: BLE001 - the queued job retries it; the sale is recorded
            log.exception("consumption of imported sale %s left to the job queue", sale.id)
    row.status = "posted"
    row.days_posted = written
    row.posted_at = datetime.now(UTC)
    db.flush()
    return written
