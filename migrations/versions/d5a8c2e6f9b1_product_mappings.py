"""Product mappings (T3.04; DM-13's mapping half): a POS variation to a product size, a POS
modifier to a modifier, one active mapping per external id.

Expand + backfill. Each external product whose sold item already became a product size (DM-9's
`source_sold_item_id`) is mapped to that size, made by the product (no person). `reprocess_mapping`
becomes a kind of job.

Revision ID: d5a8c2e6f9b1
Revises: b7d2f4c8e1a6
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import UTCDateTime

revision = "d5a8c2e6f9b1"
down_revision = "b7d2f4c8e1a6"
branch_labels = None
depends_on = None

KINDS = (
    "'evaluate_exceptions', 'send_alert_digest', 'send_weekly_report', 'extract_document',"
    " 'verify_ledger', 'consume_sale', 'connection_revoked_email'"
)
PENDING = "status = 'pending' AND dedupe_key IS NOT NULL"
ACTIVE_PRODUCT = "status = 'active' AND external_product_id IS NOT NULL"
ACTIVE_MODIFIER = "status = 'active' AND external_modifier_id IS NOT NULL"
# A Square sold item's external product (same business, same variation id) and the size made from
# that sold item.
BACKFILL = (
    "INSERT INTO product_mappings"
    " (tenant_id, external_product_id, product_variation_id, status, mapped_at)"
    " SELECT e.tenant_id, e.id, v.id, 'active', CURRENT_TIMESTAMP"
    " FROM external_products e"
    " JOIN sold_items s ON s.tenant_id = e.tenant_id AND s.provider = 'square'"
    " AND s.provider_item_id = e.external_variation_id"
    " JOIN product_variations v ON v.source_sold_item_id = s.id"
)


def _job_kinds(kinds: str) -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_kind", type_="check")
        batch.create_check_constraint("ck_jobs_kind", f"kind IN ({kinds})")
    # Rebuilt explicitly: a table rebuild must not leave this index without its WHERE.
    op.drop_index("uq_jobs_pending", table_name="jobs")
    op.create_index(
        "uq_jobs_pending",
        "jobs",
        ["tenant_id", "kind", "dedupe_key"],
        unique=True,
        sqlite_where=sa.text(PENDING),
        postgresql_where=sa.text(PENDING),
    )


def upgrade() -> None:
    op.create_table(
        "product_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("external_product_id", sa.Integer(), nullable=True),
        sa.Column("external_modifier_id", sa.Integer(), nullable=True),
        sa.Column("product_variation_id", sa.Integer(), nullable=True),
        sa.Column("modifier_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("mapped_by_user_id", sa.Integer(), nullable=True),
        sa.Column("mapped_at", UTCDateTime(), nullable=False),
        sa.Column("superseded_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint("status IN ('active', 'superseded')", name="ck_product_mappings_status"),
        sa.CheckConstraint(
            "(external_product_id IS NOT NULL AND product_variation_id IS NOT NULL"
            " AND external_modifier_id IS NULL AND modifier_id IS NULL)"
            " OR (external_modifier_id IS NOT NULL AND modifier_id IS NOT NULL"
            " AND external_product_id IS NULL AND product_variation_id IS NULL)",
            name="ck_product_mappings_target",
        ),
        sa.CheckConstraint(
            "status <> 'superseded' OR superseded_at IS NOT NULL",
            name="ck_product_mappings_superseded_at",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="product_mappings_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["external_product_id"],
            ["external_products.id"],
            name="product_mappings_external_product_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["external_modifier_id"],
            ["external_modifiers.id"],
            name="product_mappings_external_modifier_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_variation_id"],
            ["product_variations.id"],
            name="product_mappings_product_variation_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["modifier_id"],
            ["modifiers.id"],
            name="product_mappings_modifier_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["mapped_by_user_id"],
            ["users.id"],
            name="product_mappings_mapped_by_user_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_mappings_tenant_id", "product_mappings", ["tenant_id"])
    op.create_index(
        "ix_product_mappings_external_product_id", "product_mappings", ["external_product_id"]
    )
    op.create_index(
        "ix_product_mappings_external_modifier_id", "product_mappings", ["external_modifier_id"]
    )
    op.create_index(
        "uq_product_mappings_active_product",
        "product_mappings",
        ["external_product_id"],
        unique=True,
        sqlite_where=sa.text(ACTIVE_PRODUCT),
        postgresql_where=sa.text(ACTIVE_PRODUCT),
    )
    op.create_index(
        "uq_product_mappings_active_modifier",
        "product_mappings",
        ["external_modifier_id"],
        unique=True,
        sqlite_where=sa.text(ACTIVE_MODIFIER),
        postgresql_where=sa.text(ACTIVE_MODIFIER),
    )
    op.execute(BACKFILL)
    _job_kinds(KINDS + ", 'reprocess_mapping'")


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'reprocess_mapping'")
    _job_kinds(KINDS)
    op.drop_table("product_mappings")
