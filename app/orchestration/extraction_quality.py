"""How much the reader gets wrong, measured from what reviewers actually change.

"Did the model return JSON" is not a quality measure. The measure that matters is how much work
a store owner still has to do after photographing an invoice, and that is exactly what the
corrections recorded during review add up to.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, DocumentLine, DocumentStatus, ExtractionCorrection

# Read out in the order a reviewer meets them.
FIELD_LABELS = {
    "product_match": "Matched to the wrong item",
    "quantity": "Quantity",
    "cost": "Cost",
    "expiry": "Best-before date",
    "supplier": "Supplier",
    "invoice_number": "Invoice number",
}


def correction_stats(
    db: Session, tenant_id: int, days: int = 90, now: datetime | None = None
) -> dict:
    """Corrections per field, per supplier, and the lines that keep coming back wrong."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)

    documents = list(
        db.scalars(
            select(Document).where(
                Document.tenant_id == tenant_id,
                Document.status == DocumentStatus.APPLIED,
                Document.uploaded_at >= since,
            )
        )
    )
    document_ids = [document.id for document in documents]
    if not document_ids:
        return {
            "days": days,
            "documents": 0,
            "lines": 0,
            "corrections": 0,
            "lines_touched": 0,
            "clean_rate": None,
            "by_field": {},
            "suppliers": [],
            "products": [],
        }

    lines = list(db.scalars(select(DocumentLine).where(DocumentLine.document_id.in_(document_ids))))
    corrections = list(
        db.scalars(
            select(ExtractionCorrection).where(ExtractionCorrection.document_id.in_(document_ids))
        )
    )

    line_names = {line.id: line.parsed_name for line in lines}
    supplier_of = {document.id: (document.supplier or "unknown supplier") for document in documents}

    by_field: dict[str, dict] = {}
    for field, label in FIELD_LABELS.items():
        hits = [row for row in corrections if row.field == field]
        if not hits:
            continue
        # Per-line fields are judged against lines, document-level fields against documents.
        denominator = len(documents) if field in ("supplier", "invoice_number") else len(lines)
        by_field[field] = {
            "label": label,
            "count": len(hits),
            "rate": round(len(hits) / denominator, 3) if denominator else 0.0,
        }

    per_supplier = Counter(supplier_of[row.document_id] for row in corrections)
    documents_per_supplier = Counter(supplier_of[document.id] for document in documents)
    suppliers: list[dict[str, Any]] = [
        {
            "supplier": supplier,
            "documents": documents_per_supplier[supplier],
            "corrections": count,
            "per_document": round(count / documents_per_supplier[supplier], 2),
        }
        for supplier, count in per_supplier.most_common()
    ]
    suppliers.sort(key=lambda row: row["per_document"], reverse=True)

    # The same product name corrected again and again is a matching rule worth writing.
    repeat = Counter(
        line_names.get(row.document_line_id, "")
        for row in corrections
        if row.field in ("product_match", "quantity") and row.document_line_id
    )
    products = [
        {"name": name, "corrections": count}
        for name, count in repeat.most_common(10)
        if name and count > 1
    ]

    touched = {row.document_line_id for row in corrections if row.document_line_id}
    return {
        "days": days,
        "documents": len(documents),
        "lines": len(lines),
        "corrections": len(corrections),
        "lines_touched": len(touched),
        # The number that answers "does this save time": lines nobody had to touch.
        "clean_rate": round(1 - len(touched) / len(lines), 3) if lines else None,
        "by_field": by_field,
        "suppliers": suppliers[:10],
        "products": products,
    }


def correction_summary(stats: dict) -> str:
    """One sentence for a log line or a CLI."""
    if not stats["documents"]:
        return f"no invoices applied in the last {stats['days']} days"
    worst = max(stats["by_field"].values(), key=lambda data: data["count"], default=None)
    head = (
        f"{stats['corrections']} corrections across {stats['lines']} lines "
        f"on {stats['documents']} invoices"
    )
    return f"{head}; most often: {worst['label'].lower()}" if worst else head
