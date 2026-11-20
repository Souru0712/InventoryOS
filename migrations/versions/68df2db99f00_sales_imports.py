"""Sales file import (T2.13, STD-FR-03): saved column mappings, learned names, uploaded files.

Additive.

Revision ID: 68df2db99f00
Revises: e557269f9eb1
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "68df2db99f00"
down_revision = "e557269f9eb1"
branch_labels = None
depends_on = None


def _fk(table: str, column: str, target: str):
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"{table}_{column}_fkey", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "import_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("header_key", sa.String(length=500), nullable=False),
        sa.Column("date_column", sa.String(length=120), nullable=False),
        sa.Column("product_column", sa.String(length=120), nullable=False),
        sa.Column("quantity_column", sa.String(length=120), nullable=False),
        sa.Column("date_format", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("import_mappings", "tenant_id", "tenants.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "header_key", name="uq_import_mappings_header"),
    )
    op.create_index("ix_import_mappings_tenant_id", "import_mappings", ["tenant_id"])
    op.create_table(
        "import_names",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("product_variation_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("import_names", "tenant_id", "tenants.id"),
        _fk("import_names", "product_variation_id", "product_variations.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_import_names_name"),
    )
    op.create_index("ix_import_names_tenant_id", "import_names", ["tenant_id"])
    op.create_table(
        "sales_imports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("mapping_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("days_posted", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('preview', 'posted')", name="ck_sales_imports_status"),
        _fk("sales_imports", "tenant_id", "tenants.id"),
        _fk("sales_imports", "location_id", "locations.id"),
        _fk("sales_imports", "mapping_id", "import_mappings.id"),
        _fk("sales_imports", "uploaded_by", "users.id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_imports_tenant_id", "sales_imports", ["tenant_id"])


def downgrade() -> None:
    for table in ("sales_imports", "import_names", "import_mappings"):
        op.drop_table(table)
