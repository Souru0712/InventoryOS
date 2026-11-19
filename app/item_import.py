"""Item import and quick-add (T2.20-a; INV-FR-08, AC-INV-12).

A CSV is kept in the business's storage under its digest; the preview tries every row through
`create_item()` inside a savepoint that is rolled back, so each row's error is the one importing
would meet. Importing keeps the rows that pass and reports the rest; quick-add runs the same way
on rows typed into a grid. Items are made only by `app/items.py`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped
from app.config import get_settings
from app.items import NewItem, create_item
from app.items import NotFound as ItemNotFound
from app.models import Location
from app.storage import StorageError, get_storage
from app.transform.imports import ImportRuleError, read_csv
from app.transform.item_import import ItemImportError, ItemRow, item_rows, row_problem

log = logging.getLogger(__name__)
_DIGEST = re.compile(r"[0-9a-f]{64}")


class NotFound(LookupError):
    """No such file or location for this business."""


@dataclass(frozen=True)
class RowResult:
    line: int
    name: str
    unit: str
    error: str | None = None  # None: imports (or imported)


def _key(org: OrgContext, digest: str) -> str:
    if not _DIGEST.fullmatch(digest):
        raise NotFound(digest)
    return f"{org.tenant_id}/imports/items-{digest}.csv"


def upload_items(db: Session, org: OrgContext, content: bytes) -> str:
    """Keep an item file for preview; its digest names it. The file's type comes from its
    bytes: a spreadsheet is refused with what to do instead."""
    if len(content) > get_settings().max_upload_mb * 1024 * 1024:
        raise ItemImportError(f"Files are at most {get_settings().max_upload_mb} MB.")
    if content[:4] == b"PK\x03\x04":
        raise ItemImportError("Excel files are not read yet: save it as CSV and upload that.")
    if b"\x00" in content[:4096]:
        raise ItemImportError("That is not a CSV file.")
    try:
        header, rows = read_csv(content)
    except ImportRuleError as exc:
        raise ItemImportError(str(exc)) from exc
    if not item_rows(header, rows):
        raise ItemImportError("The file has no items in it.")
    digest = hashlib.sha256(content).hexdigest()
    get_storage().put(_key(org, digest), content, "text/csv")
    log.info("item file %s kept for tenant %s", digest[:12], org.tenant_id)
    return digest


def file_rows(org: OrgContext, digest: str) -> list[ItemRow]:
    try:
        content = get_storage().get(_key(org, digest))
    except StorageError as exc:
        raise NotFound(digest) from exc
    header, rows = read_csv(content)
    return item_rows(header, rows)


def _new_item(row: ItemRow, location_id: int) -> NewItem:
    values = row.values
    return NewItem(
        name=row.name,
        location_ids=[location_id],
        dimension=row.dimension,
        display_unit=row.unit,
        item_type=row.item_type,
        category=values.get("category", ""),
        quantity=values.get("quantity") or "0",
        reorder_point=values.get("reorder_point") or "0",
        par_level=values.get("par_level", ""),
        cost_per_unit=values.get("cost") or "0",
        supplier=values.get("supplier", ""),
    )


def _try(
    db: Session, org: OrgContext, rows: list[ItemRow], location_id: int, keep: bool
) -> list[RowResult]:
    if get_scoped(db, org, Location, location_id) is None:
        raise NotFound(location_id)
    results = []
    seen: set[str] = set()
    for row in rows:
        problem = row_problem(row)
        if problem is None and row.name.casefold() in seen:
            problem = "This name is in the file twice."
        seen.add(row.name.casefold())
        if problem is None:
            nested = db.begin_nested()
            try:
                create_item(db, org, _new_item(row, location_id))
            except ItemNotFound as exc:
                nested.rollback()
                raise NotFound(location_id) from exc
            except (ValueError, ArithmeticError) as exc:
                nested.rollback()
                problem = str(exc) or "This row could not be read."
            else:
                if keep:
                    nested.commit()
                else:
                    nested.rollback()
        results.append(RowResult(row.line, row.name, row.unit, problem))
    return results


def preview_items(db: Session, org: OrgContext, digest: str, location_id: int) -> list[RowResult]:
    """Every row, and what would stop it importing (nothing is kept)."""
    return _try(db, org, file_rows(org, digest), location_id, keep=False)


def import_items(db: Session, org: OrgContext, digest: str, location_id: int) -> list[RowResult]:
    """Make the items whose rows pass; the rest come back with their reasons (AC-INV-12)."""
    results = _try(db, org, file_rows(org, digest), location_id, keep=True)
    log.info(
        "imported %d items for tenant %s, %d rows refused",
        sum(1 for r in results if r.error is None),
        org.tenant_id,
        sum(1 for r in results if r.error is not None),
    )
    return results


def quick_add(
    db: Session, org: OrgContext, rows: list[ItemRow], location_id: int
) -> list[RowResult]:
    """Items typed into the quick-add grid, kept or refused row by row (INV-FR-08)."""
    return _try(db, org, rows, location_id, keep=True)
