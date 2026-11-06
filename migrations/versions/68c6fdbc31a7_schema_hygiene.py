"""Schema hygiene (DM-2): aware timestamps, CHECK constraints, explicit delete rules.

Every instant becomes timestamptz on Postgres, read as UTC as it always was. Foreign keys say
what deleting their target does: RESTRICT, or CASCADE for a part of its parent. CHECKs refuse
the negative quantities and unknown sources the audit found accepted (DB1, DB3).

Revision ID: 68c6fdbc31a7
Revises: 5c1d0f2a9b31
Create Date: 2026-11-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "68c6fdbc31a7"
down_revision = "5c1d0f2a9b31"
branch_labels = None
depends_on = None

# Frozen here, not read from the models: later model changes must not rewrite this migration.
TIMESTAMPS = {
    "email_suppressions": ("created_at",),
    "tenants": (
        "trial_started_at",
        "trial_ends_at",
        "current_period_start",
        "current_period_end",
        "past_due_since",
        "stripe_event_at",
        "created_at",
    ),
    "email_log": ("sent_at",),
    "health_snapshots": ("created_at",),
    "provider_connections": ("expires_at", "created_at"),
    "sync_runs": ("started_at", "finished_at"),
    "users": ("created_at",),
    "webhook_events": ("received_at",),
    "documents": ("uploaded_at", "applied_at"),
    "stock_items": ("last_counted_at", "last_synced_at"),
    "alerts": ("created_at", "notified_at", "resolved_at"),
    "inventory_counts": ("counted_at",),
    "inventory_events": ("created_at",),
    "product_identifiers": ("created_at",),
    "stock_aliases": ("created_at",),
    "extraction_corrections": ("created_at",),
    "stock_batches": ("received_at",),
    "promotions": ("created_at", "ended_at"),
}

RESTRICT, CASCADE = "RESTRICT", "CASCADE"
FOREIGN_KEYS = {
    "email_log": (("tenant_id", "tenants", RESTRICT),),
    "health_snapshots": (("tenant_id", "tenants", RESTRICT),),
    "locations": (("tenant_id", "tenants", RESTRICT),),
    "provider_connections": (("tenant_id", "tenants", RESTRICT),),
    "sold_items": (("tenant_id", "tenants", RESTRICT),),
    "sync_runs": (("tenant_id", "tenants", RESTRICT),),
    "users": (("tenant_id", "tenants", RESTRICT),),
    "webhook_events": (("tenant_id", "tenants", RESTRICT),),
    "documents": (
        ("location_id", "locations", RESTRICT),
        ("tenant_id", "tenants", RESTRICT),
        ("uploaded_by", "users", RESTRICT),
    ),
    "sales_daily": (
        ("location_id", "locations", RESTRICT),
        ("sold_item_id", "sold_items", RESTRICT),
        ("tenant_id", "tenants", RESTRICT),
    ),
    "stock_items": (
        ("location_id", "locations", RESTRICT),
        ("tenant_id", "tenants", RESTRICT),
    ),
    "alerts": (
        ("stock_item_id", "stock_items", RESTRICT),
        ("tenant_id", "tenants", RESTRICT),
    ),
    "document_lines": (
        ("document_id", "documents", CASCADE),
        ("stock_item_id", "stock_items", RESTRICT),
        ("tenant_id", "tenants", RESTRICT),
    ),
    "inventory_counts": (
        ("stock_item_id", "stock_items", RESTRICT),
        ("tenant_id", "tenants", RESTRICT),
    ),
    "inventory_events": (
        ("stock_item_id", "stock_items", RESTRICT),
        ("tenant_id", "tenants", RESTRICT),
        ("user_id", "users", RESTRICT),
    ),
    "product_identifiers": (
        ("stock_item_id", "stock_items", RESTRICT),
        ("tenant_id", "tenants", RESTRICT),
    ),
    "recipe_components": (
        ("sold_item_id", "sold_items", CASCADE),
        ("stock_item_id", "stock_items", RESTRICT),
        ("tenant_id", "tenants", RESTRICT),
    ),
    "stock_aliases": (
        ("stock_item_id", "stock_items", RESTRICT),
        ("tenant_id", "tenants", RESTRICT),
    ),
    "extraction_corrections": (
        ("corrected_by", "users", RESTRICT),
        ("document_id", "documents", CASCADE),
        ("document_line_id", "document_lines", CASCADE),
        ("tenant_id", "tenants", RESTRICT),
    ),
    "stock_batches": (
        ("document_line_id", "document_lines", RESTRICT),
        ("stock_item_id", "stock_items", RESTRICT),
        ("tenant_id", "tenants", RESTRICT),
    ),
    "promotions": (
        ("decided_by", "users", RESTRICT),
        ("stock_batch_id", "stock_batches", RESTRICT),
        ("stock_item_id", "stock_items", RESTRICT),
        ("tenant_id", "tenants", RESTRICT),
    ),
}

CHECKS = {
    "email_suppressions": (
        ("ck_email_suppressions_source", "source IN ('bounce', 'complaint', 'manual')"),
    ),
    "stock_items": (
        (
            "ck_stock_items_reorder_point_base_not_negative",
            "CAST(reorder_point_base AS NUMERIC) >= 0",
        ),
        ("ck_stock_items_par_level_base_not_negative", "CAST(par_level_base AS NUMERIC) >= 0"),
        ("ck_stock_items_pack_size_base_positive", "CAST(pack_size_base AS NUMERIC) > 0"),
        ("ck_stock_items_lead_time_days_not_negative", "CAST(lead_time_days AS NUMERIC) >= 0"),
    ),
    "inventory_counts": (
        ("ck_inventory_counts_source", "source IN ('manual', 'opening', 'receipt', 'scan')"),
    ),
    "product_identifiers": (
        ("ck_product_identifiers_source", "source IN ('manual', 'square', 'scan')"),
    ),
    "recipe_components": (
        (
            "ck_recipe_components_qty_base_per_sale_positive",
            "CAST(qty_base_per_sale AS NUMERIC) > 0",
        ),
    ),
    "stock_batches": (
        ("ck_stock_batches_received_qty_base_positive", "CAST(received_qty_base AS NUMERIC) > 0"),
        ("ck_stock_batches_source", "source IN ('receipt', 'label', 'scan')"),
    ),
}

# SQLite foreign keys made by earlier migrations have no name; batch mode names them by this
# pattern on reflection so they can be dropped.
SQLITE_NAMES = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _fk_name(table: str, column: str, referent: str) -> str:
    """The current name of the foreign key on table.column, as this database knows it."""
    for fk in sa.inspect(op.get_bind()).get_foreign_keys(table):
        if fk["constrained_columns"] == [column]:
            return fk["name"] or f"fk_{table}_{column}_{referent}"
    raise RuntimeError(f"no foreign key on {table}.{column}; the schema is not at 5c1d0f2a9b31")


def _rebuild_foreign_keys(table: str, batch, with_rules: bool) -> None:
    for column, referent, rule in FOREIGN_KEYS.get(table, ()):
        batch.drop_constraint(_fk_name(table, column, referent), type_="foreignkey")
        batch.create_foreign_key(
            f"{table}_{column}_fkey",
            referent,
            [column],
            ["id"],
            ondelete=rule if with_rules else None,
        )


def _tables() -> list[str]:
    return list(FOREIGN_KEYS) + [name for name in CHECKS if name not in FOREIGN_KEYS]


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # The stored wall clock was always UTC (the session ran in UTC), so it is read as UTC.
        for table, columns in TIMESTAMPS.items():
            for column in columns:
                op.alter_column(
                    table,
                    column,
                    type_=sa.DateTime(timezone=True),
                    existing_type=sa.DateTime(),
                    postgresql_using=f"{column} AT TIME ZONE 'UTC'",
                )
    # SQLite stores the same text either way; it reads the zone back in app.db.UTCDateTime.

    for table in _tables():
        with op.batch_alter_table(table, naming_convention=SQLITE_NAMES) as batch:
            _rebuild_foreign_keys(table, batch, with_rules=True)
            for name, condition in CHECKS.get(table, ()):
                batch.create_check_constraint(name, condition)


def downgrade() -> None:
    for table in reversed(_tables()):
        with op.batch_alter_table(table, naming_convention=SQLITE_NAMES) as batch:
            for name, _condition in CHECKS.get(table, ()):
                batch.drop_constraint(name, type_="check")
            _rebuild_foreign_keys(table, batch, with_rules=False)

    if op.get_bind().dialect.name == "postgresql":
        for table, columns in TIMESTAMPS.items():
            for column in columns:
                op.alter_column(
                    table,
                    column,
                    type_=sa.DateTime(),
                    existing_type=sa.DateTime(timezone=True),
                    postgresql_using=f"{column} AT TIME ZONE 'UTC'",
                )
