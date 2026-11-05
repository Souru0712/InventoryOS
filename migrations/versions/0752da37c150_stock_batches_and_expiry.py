"""stock batches and expiry

Revision ID: 0752da37c150
Revises: 66bc17b36008
Create Date: 2026-09-10 19:12:37.889645
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.db import Qty

revision: str = "0752da37c150"
down_revision: str | None = "66bc17b36008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_ALERT_KINDS = ("WASTE_RISK", "EXPIRED")


def upgrade() -> None:
    # Autogenerate does not see new enum members. On SQLite the column is a VARCHAR wide enough
    # already; on Postgres alertkind is a native type, and ADD VALUE must run outside a transaction.
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for kind in NEW_ALERT_KINDS:
                op.execute(f"ALTER TYPE alertkind ADD VALUE IF NOT EXISTS '{kind}'")

    op.create_table(
        "stock_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("stock_item_id", sa.Integer(), nullable=False),
        sa.Column("received_qty_base", Qty(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("cost_per_base_cents", Qty(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("document_line_id", sa.Integer(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_line_id"],
            ["document_lines.id"],
        ),
        sa.ForeignKeyConstraint(
            ["stock_item_id"],
            ["stock_items.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("stock_batches", schema=None) as batch_op:
        batch_op.create_index(
            "ix_batches_item_expiry", ["stock_item_id", "expires_on"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_stock_batches_stock_item_id"), ["stock_item_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_stock_batches_tenant_id"), ["tenant_id"], unique=False)

    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("value_cents", sa.Integer(), nullable=True))

    with op.batch_alter_table("document_lines", schema=None) as batch_op:
        batch_op.add_column(sa.Column("expires_on", sa.Date(), nullable=True))

    with op.batch_alter_table("stock_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sku", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("upc", sa.String(length=32), nullable=True))


def downgrade() -> None:
    # The previous code cannot load these kinds. Postgres cannot drop enum values, so the type
    # keeps them; that is harmless once no row uses them.
    kinds = ", ".join(f"'{kind}'" for kind in NEW_ALERT_KINDS)
    op.execute(f"DELETE FROM alerts WHERE kind IN ({kinds})")

    with op.batch_alter_table("stock_items", schema=None) as batch_op:
        batch_op.drop_column("upc")
        batch_op.drop_column("sku")

    with op.batch_alter_table("document_lines", schema=None) as batch_op:
        batch_op.drop_column("expires_on")

    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.drop_column("value_cents")

    with op.batch_alter_table("stock_batches", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_stock_batches_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_stock_batches_stock_item_id"))
        batch_op.drop_index("ix_batches_item_expiry")

    op.drop_table("stock_batches")
