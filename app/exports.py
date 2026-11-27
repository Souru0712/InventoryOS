"""A business's data, exported whole (T4.05, ADR-0092; ADM-FR-10, SAAS-D-01, SAAS-D-02, AC-ADM-03).

An Owner, Administrator or Manager asks for an export at any time, read-only included (SAAS-D-02).
A job writes one CSV per table of the business's records, from items and recipes to the ledger,
receivings, waste, counts, exceptions and sales, plus every invoice file, into a zip kept in the
upload storage. Only the person who asked can download it, for three days; then the file is
deleted. A full export needs access to every location (PD-58): rows without a location of their
own (lines, recipes) could otherwise carry other locations' data. Columns holding secrets are
never written; money columns are left out for someone who does not see costs.
"""

from __future__ import annotations

import csv
import enum
import io
import json
import logging
import re
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, members, select_scoped
from app.access.permissions import VIEW_COSTS
from app.audit import record_audit
from app.config import get_settings
from app.db import Base
from app.jobs import enqueue
from app.models import DataExport, Document, Tenant
from app.notify.email import render_pair, send_email
from app.storage import StorageError, get_storage

log = logging.getLogger(__name__)

JOB = "export_data"
KEEP = timedelta(days=3)

# Every table of the business's own records, in the order a reader meets them (SAAS-D-01).
# Named, not imported: the export reads every table and writes none, and the ledger and the
# audit trail are named only by their own writers (`app/ledger/posting.py`, `app/audit.py`).
TABLES: tuple[str, ...] = (
    "locations",
    "location_days",
    "business_settings",
    "inventory_categories",
    "inventory_items",
    "item_locations",
    "item_unit_conversions",
    "item_packages",
    "product_identifiers",
    "stock_items",
    "suppliers",
    "supplier_items",
    "products",
    "product_variations",
    "modifiers",
    "modifier_scales",
    "recipes",
    "recipe_versions",
    "recipe_lines",
    "external_locations",
    "external_products",
    "external_modifiers",
    "product_mappings",
    "inventory_transactions",
    "inventory_movements",
    "stock_balances",
    "stock_batches",
    "item_cost_history",
    "inventory_adjustments",
    "receivings",
    "receiving_lines",
    "documents",
    "document_lines",
    "waste_reasons",
    "waste_events",
    "waste_lines",
    "stock_counts",
    "stock_count_lines",
    "production_orders",
    "promotions",
    "exceptions",
    "sales_sources",
    "sales_transactions",
    "sales_transaction_lines",
    "sales_line_modifiers",
    "sales_returns",
    "sales_return_lines",
    "sales_imports",
    "audit_events",
)
SECRET = re.compile(r"(_enc$|token|secret|password|hash)", re.I)
MONEY = re.compile(r"(cost|price|_cents$|value)", re.I)
EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}


class ExportRefused(ValueError):
    """An export this member may not ask for; the message says why."""


def request_export(
    db: Session, org: OrgContext, user_id: int, *, ip: str | None = None
) -> DataExport:
    """Ask for a full export; one already being prepared is the answer to asking again."""
    if org.location_ids is not None:
        raise ExportRefused(
            "A full export needs access to every location. Ask an Owner or Administrator."
        )
    waiting = db.scalars(
        select_scoped(org, DataExport).where(DataExport.status == "pending")
    ).first()
    if waiting is not None:
        return waiting
    row = DataExport(tenant_id=org.tenant_id, requested_by=user_id, with_costs=org.can(VIEW_COSTS))
    db.add(row)
    db.flush()
    enqueue(db, org, JOB, {"export_id": row.id}, dedupe_key=f"export:{row.id}")
    record_audit(
        db,
        org,
        "data.export_requested",
        "Asked for a full data export",
        subject_type="data_export",
        subject_id=row.id,
        after={"with_costs": row.with_costs},
        ip=ip,
    )
    return row


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, enum.Enum):
        value = value.value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{value.normalize():f}"
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True)
    text = str(value)
    # A name like '=HYPERLINK(...)' must not run as a formula when the file is opened.
    if text[:1] in ("=", "+", "-", "@"):
        try:
            Decimal(text)
        except ArithmeticError:
            return f"'{text}"
    return text


def model_of(table: str) -> type[Base]:
    """The mapped class of a table, from the registry."""
    for mapper in Base.registry.mappers:
        if getattr(mapper.class_, "__tablename__", None) == table:
            return mapper.class_
    raise KeyError(table)


def table_csv(
    db: Session, org: OrgContext, model: type[Base], *, with_costs: bool
) -> tuple[str, int]:
    """One table as CSV text, and its row count; secrets never, money only with costs."""
    columns = [
        column
        for column in model.__table__.columns
        if not SECRET.search(column.name) and (with_costs or not MONEY.search(column.name))
    ]
    order = list(model.__table__.primary_key)
    query = select_scoped(org, model).with_only_columns(*columns).order_by(*order)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([column.name for column in columns])
    count = 0
    for row in db.execute(query):
        writer.writerow([_cell(value) for value in row])
        count += 1
    return out.getvalue(), count


def build(db: Session, org: OrgContext, export_id: int) -> DataExport | None:
    """The job's work: write the zip, keep it, tell the person who asked. Safe to repeat."""
    row = get_scoped(db, org, DataExport, export_id)
    if row is None or row.status != "pending":
        return row
    tenant = get_scoped(db, org, Tenant, org.tenant_id)
    storage = get_storage()
    now = datetime.now(UTC)
    buffer = io.BytesIO()
    counts: list[str] = []
    missing: list[str] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in TABLES:
            text, count = table_csv(db, org, model_of(name), with_costs=row.with_costs)
            archive.writestr(f"{name}.csv", text)
            counts.append(f"  {name}.csv: {count} row{'' if count == 1 else 's'}")
        for document in db.scalars(select_scoped(org, Document).order_by(Document.id)):
            extension = EXTENSIONS.get(document.content_type, "")
            try:
                archive.writestr(
                    f"invoices/{document.id}{extension}", storage.get(document.storage_key)
                )
            except StorageError:
                missing.append(f"  invoices/{document.id}{extension}")
        archive.writestr("README.txt", _readme(tenant, now, row.with_costs, counts, missing))
    data = buffer.getvalue()
    key = f"exports/{row.tenant_id}/{row.id}.zip"
    try:
        storage.put(key, data, "application/zip")
    except StorageError as exc:
        row.status, row.error, row.finished_at = "failed", str(exc)[:300], now
        log.error("export %s for tenant %s could not be kept: %s", row.id, row.tenant_id, exc)
        db.flush()
        return row
    row.status, row.storage_key, row.size_bytes = "ready", key, len(data)
    row.finished_at, row.expires_at = now, now + KEEP
    db.flush()
    _tell(db, org, row)
    log.info("export %s for tenant %s is ready (%d bytes)", row.id, row.tenant_id, len(data))
    return row


def _readme(
    tenant: Tenant | None, now: datetime, with_costs: bool, counts: list[str], missing: list[str]
) -> str:
    lines = [
        f"Vontry data export for {tenant.name if tenant else 'your business'}",
        f"Made {now:%Y-%m-%d %H:%M} UTC.",
        "",
        "One CSV per table, with every row of your business's records; invoice files under",
        "invoices/, named by their id in documents.csv. Times are UTC.",
    ]
    if not with_costs:
        lines.append(
            "Costs, prices and values are left out: the person who asked does not see costs."
        )
    lines += ["", "Files:", *counts]
    if missing:
        lines += ["", "Invoice files that could not be read from storage:", *missing]
    return "\n".join(lines) + "\n"


def _tell(db: Session, org: OrgContext, row: DataExport) -> None:
    person = next((user for user in members(db, org) if user.id == row.requested_by), None)
    if person is None:
        return
    html, text = render_pair(
        "email/data_export",
        link=f"{get_settings().app_base_url}/app/exports",
        expires_at=row.expires_at,
    )
    send_email(
        db,
        org.tenant_id,
        person.email,
        "Your Vontry data export is ready",
        html,
        "data_export",
        text,
    )


def downloadable(row: DataExport, user_id: int, now: datetime | None = None) -> bool:
    """Ready, not expired, and asked for by this person."""
    now = now or datetime.now(UTC)
    return (
        row.status == "ready"
        and row.requested_by == user_id
        and row.expires_at is not None
        and now < row.expires_at
        and row.storage_key is not None
    )


def expire(db: Session, rows: list[DataExport]) -> int:
    """Expired exports lose their file: it holds every record of the business."""
    storage = get_storage()
    for row in rows:
        if row.storage_key:
            try:
                storage.delete(row.storage_key)
            except StorageError as exc:
                log.error("could not delete export %s: %s", row.id, exc)
                continue
        row.status, row.storage_key = "expired", None
    db.flush()
    return len(rows)


def history(db: Session, org: OrgContext) -> list[DataExport]:
    return list(db.scalars(select_scoped(org, DataExport).order_by(DataExport.id.desc()).limit(20)))
