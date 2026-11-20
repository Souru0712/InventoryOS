"""Receivings (T2.11-a, DM-12): `receivings` and `receiving_lines`, history for applied invoices.

Expand + backfill. Every invoice document already applied gets a posted receiving with a line per
applied document line (accepted = what was received, in base units), linked to the supplier of
the same name and to the receipt transaction already posted for it. No movement is written: the
stock already arrived (DM-12, history only). Running it again adds nothing: a document that has a
receiving is skipped.

Revision ID: ebcbee00f30e
Revises: 30d75bcd3fd3
Create Date: 2026-11-19
"""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.db import Qty, UTCDateTime

revision = "ebcbee00f30e"
down_revision = "30d75bcd3fd3"
branch_labels = None
depends_on = None

receivings = sa.table(
    "receivings",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("location_id"),
    sa.column("supplier_id"),
    sa.column("document_id"),
    sa.column("status"),
    sa.column("created_by_user_id"),
    sa.column("created_at", UTCDateTime()),
    sa.column("posted_at", UTCDateTime()),
    sa.column("posted_by_user_id"),
)
lines = sa.table(
    "receiving_lines",
    sa.column("tenant_id"),
    sa.column("receiving_id"),
    sa.column("inventory_item_id"),
    sa.column("description"),
    sa.column("invoiced_qty", Qty()),
    sa.column("arrived_qty", Qty()),
    sa.column("accepted_qty", Qty()),
    sa.column("unit"),
    sa.column("qty_base", Qty()),
    sa.column("expires_on", sa.Date()),
    sa.column("price_flag"),
    sa.column("status"),
    sa.column("transaction_id"),
)


def _fk(table: str, column: str, target: str, on_delete: str | None = "RESTRICT"):
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"{table}_{column}_fkey", ondelete=on_delete
    )


def backfill(connection: sa.Connection) -> int:
    """Posted receivings for applied documents; the number made."""
    done = {
        row.document_id
        for row in connection.execute(sa.select(receivings.c.document_id))
        if row.document_id is not None
    }
    suppliers = {
        (row.tenant_id, (row.name or "").strip().casefold()): row.id
        for row in connection.execute(sa.text("SELECT id, tenant_id, name FROM suppliers"))
    }
    documents = connection.execute(
        sa.text(
            "SELECT id, tenant_id, location_id, supplier, uploaded_by, uploaded_at, applied_at"
            " FROM documents WHERE status = 'APPLIED' ORDER BY id"
        ).columns(uploaded_at=UTCDateTime(), applied_at=UTCDateTime())
    ).all()
    made = 0
    for doc in documents:
        if doc.id in done:
            continue
        posting = connection.execute(
            sa.text(
                "SELECT id FROM inventory_transactions WHERE tenant_id = :t AND idempotency_key = :k"
            ),
            {"t": doc.tenant_id, "k": f"receipt:document:{doc.id}"},
        ).scalar()
        receiving_id = connection.execute(
            sa.insert(receivings)
            .values(
                tenant_id=doc.tenant_id,
                location_id=doc.location_id,
                supplier_id=suppliers.get((doc.tenant_id, (doc.supplier or "").strip().casefold())),
                document_id=doc.id,
                status="posted",
                created_by_user_id=doc.uploaded_by,
                created_at=doc.uploaded_at,
                posted_at=doc.applied_at or doc.uploaded_at,
                posted_by_user_id=doc.uploaded_by,
            )
            .returning(receivings.c.id)
        ).scalar_one()
        rows = connection.execute(
            sa.text(
                "SELECT l.parsed_name, l.received_qty_base, l.expires_on, s.inventory_item_id"
                " FROM document_lines l JOIN stock_items s ON s.id = l.stock_item_id"
                " WHERE l.document_id = :d AND l.status = 'APPLIED'"
                " AND s.inventory_item_id IS NOT NULL ORDER BY l.line_no, l.id"
            ).columns(received_qty_base=Qty(), expires_on=sa.Date()),
            {"d": doc.id},
        ).all()
        for row in rows:
            qty = Decimal(row.received_qty_base or 0)
            connection.execute(
                sa.insert(lines).values(
                    tenant_id=doc.tenant_id,
                    receiving_id=receiving_id,
                    inventory_item_id=row.inventory_item_id,
                    description=(row.parsed_name or "")[:300] or None,
                    invoiced_qty=qty,
                    arrived_qty=qty,
                    accepted_qty=qty,
                    unit="",
                    qty_base=qty,
                    expires_on=row.expires_on,
                    price_flag=False,
                    status="posted",
                    transaction_id=posting,
                )
            )
        made += 1
    return made


def upgrade() -> None:
    op.create_table(
        "receivings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=True),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_by_user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'posted')", name="ck_receivings_status"),
        _fk("receivings", "tenant_id", "tenants.id"),
        _fk("receivings", "location_id", "locations.id"),
        _fk("receivings", "supplier_id", "suppliers.id"),
        _fk("receivings", "document_id", "documents.id"),
        _fk("receivings", "created_by_user_id", "users.id"),
        _fk("receivings", "posted_by_user_id", "users.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", name="uq_receivings_document"),
    )
    op.create_index("ix_receivings_tenant_id", "receivings", ["tenant_id"])
    op.create_index("ix_receivings_supplier_id", "receivings", ["supplier_id"])
    op.create_index("ix_receivings_location_time", "receivings", ["location_id", "created_at"])
    op.create_table(
        "receiving_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("receiving_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("supplier_item_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("ordered_qty", Qty(), nullable=True),
        sa.Column("invoiced_qty", Qty(), nullable=True),
        sa.Column("arrived_qty", Qty(), nullable=False),
        sa.Column("accepted_qty", Qty(), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=False),
        sa.Column("qty_base", Qty(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("lot_code", sa.String(length=80), nullable=True),
        sa.Column("reject_reason", sa.String(length=200), nullable=True),
        sa.Column("price_flag", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=True),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        sa.Column("corrects_line_id", sa.Integer(), nullable=True),
        sa.Column("correction_reason", sa.String(length=300), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'posted', 'corrected')", name="ck_receiving_lines_status"
        ),
        sa.CheckConstraint(
            "CAST(arrived_qty AS NUMERIC) >= 0", name="ck_receiving_lines_arrived_qty_not_negative"
        ),
        sa.CheckConstraint(
            "CAST(accepted_qty AS NUMERIC) >= 0",
            name="ck_receiving_lines_accepted_qty_not_negative",
        ),
        _fk("receiving_lines", "tenant_id", "tenants.id"),
        _fk("receiving_lines", "receiving_id", "receivings.id", "CASCADE"),
        _fk("receiving_lines", "inventory_item_id", "inventory_items.id"),
        _fk("receiving_lines", "supplier_item_id", "supplier_items.id"),
        _fk("receiving_lines", "batch_id", "stock_batches.id"),
        _fk("receiving_lines", "transaction_id", "inventory_transactions.id"),
        _fk("receiving_lines", "corrects_line_id", "receiving_lines.id", None),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_receiving_lines_receiving_id", "receiving_lines", ["receiving_id"])
    backfill(op.get_bind())


def downgrade() -> None:
    op.drop_table("receiving_lines")
    op.drop_table("receivings")
