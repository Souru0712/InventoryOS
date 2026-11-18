"""Sales model (T2.04, DM-7 for `sales_daily`): sources, transactions, lines, returns.

Expand + backfill. Every location with `sales_daily` rows gets a source of record (a POS
adapter, its provider taken from its sold items, from its first sales day), and each of its
days becomes one daily transaction from that source: a line per sold item, with the units and
money of the day, linked to the item's owned variation where T2.01 made one and keeping the POS
item's reference either way. `sales_daily` is not replayed into the ledger and nothing writes it
from here (DM-7); the backfill keeps velocity, reorder and reports on the same history. Running
it again adds nothing: a location that already has a source is skipped.

Revision ID: 9c4e1b7a2d05
Revises: 7a3c9d2e4f18
Create Date: 2026-11-14
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.db import Qty, UTCDateTime

revision = "9c4e1b7a2d05"
down_revision = "7a3c9d2e4f18"
branch_labels = None
depends_on = None

daily = sa.table(
    "sales_daily",
    sa.column("tenant_id"),
    sa.column("sold_item_id"),
    sa.column("location_id"),
    sa.column("dt", sa.Date()),
    sa.column("units", Qty()),
    sa.column("gross_cents"),
    sa.column("net_cents"),
)
sold = sa.table(
    "sold_items",
    sa.column("id"),
    sa.column("provider"),
    sa.column("provider_item_id"),
    sa.column("name"),
)
variations = sa.table("product_variations", sa.column("id"), sa.column("source_sold_item_id"))
sources = sa.table(
    "sales_sources",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("location_id"),
    sa.column("kind"),
    sa.column("provider"),
    sa.column("active_from", sa.Date()),
    sa.column("active_to", sa.Date()),
    sa.column("of_record"),
    sa.column("created_at", UTCDateTime()),
)
transactions = sa.table(
    "sales_transactions",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("location_id"),
    sa.column("sales_source_id"),
    sa.column("origin"),
    sa.column("source_reference"),
    sa.column("revision"),
    sa.column("occurred_at", UTCDateTime()),
    sa.column("business_date", sa.Date()),
    sa.column("status"),
    sa.column("of_record"),
    sa.column("gross_cents"),
    sa.column("discount_cents"),
    sa.column("net_cents"),
    sa.column("tax_cents"),
    sa.column("recorded_at", UTCDateTime()),
)
lines = sa.table(
    "sales_transaction_lines",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("sales_transaction_id"),
    sa.column("line_key"),
    sa.column("product_variation_id"),
    sa.column("external_ref"),
    sa.column("description"),
    sa.column("quantity", Qty()),
    sa.column("gross_cents"),
    sa.column("net_cents"),
    sa.column("consumption_status"),
    sa.column("recipe_version_id"),
)


def backfill(connection: sa.Connection) -> int:
    """Daily transactions for every location's `sales_daily` days; the number written."""
    now = datetime.now(UTC)
    items = {row.id: row for row in connection.execute(sa.select(sold))}
    variation_of = {
        row.source_sold_item_id: row.id
        for row in connection.execute(sa.select(variations))
        if row.source_sold_item_id is not None
    }
    have_source = {row.location_id for row in connection.execute(sa.select(sources))}
    by_place: dict[tuple[int, int], list] = defaultdict(list)
    for row in connection.execute(sa.select(daily).order_by(daily.c.dt)):
        if row.location_id not in have_source and row.sold_item_id in items:
            by_place[(row.tenant_id, row.location_id)].append(row)
    written = 0
    for (tenant_id, location_id), rows in by_place.items():
        providers = Counter(items[r.sold_item_id].provider for r in rows)
        source_id = connection.execute(
            sa.insert(sources)
            .values(
                tenant_id=tenant_id,
                location_id=location_id,
                kind="pos_adapter",
                provider=providers.most_common(1)[0][0],
                active_from=min(r.dt for r in rows),
                active_to=None,
                of_record=True,
                created_at=now,
            )
            .returning(sources.c.id)
        ).scalar_one()
        by_day: dict = defaultdict(list)
        for row in rows:
            by_day[row.dt].append(row)
        for day, day_rows in sorted(by_day.items()):
            transaction_id = connection.execute(
                sa.insert(transactions)
                .values(
                    tenant_id=tenant_id,
                    location_id=location_id,
                    sales_source_id=source_id,
                    origin="pos_adapter",
                    source_reference=f"daily:{day.isoformat()}",
                    revision=1,
                    occurred_at=None,
                    business_date=day,
                    status="completed",
                    of_record=True,
                    gross_cents=sum(r.gross_cents or 0 for r in day_rows),
                    discount_cents=None,
                    net_cents=sum(r.net_cents or 0 for r in day_rows),
                    tax_cents=None,
                    recorded_at=now,
                )
                .returning(transactions.c.id)
            ).scalar_one()
            for row in day_rows:
                item = items[row.sold_item_id]
                connection.execute(
                    sa.insert(lines).values(
                        tenant_id=tenant_id,
                        sales_transaction_id=transaction_id,
                        line_key=f"item:{item.provider_item_id}"[:120],
                        product_variation_id=variation_of.get(item.id),
                        external_ref=item.provider_item_id,
                        description=item.name[:300],
                        quantity=Decimal(row.units or 0).quantize(Decimal("0.0001")),
                        gross_cents=row.gross_cents,
                        net_cents=row.net_cents,
                        consumption_status="pending",
                        recipe_version_id=None,
                    )
                )
            written += 1
    return written


def _fk(table: str, column: str, target: str, on_delete: str = "RESTRICT"):
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"{table}_{column}_fkey", ondelete=on_delete
    )


def upgrade() -> None:
    op.create_table(
        "sales_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("active_from", sa.Date(), nullable=False),
        sa.Column("active_to", sa.Date(), nullable=True),
        sa.Column("of_record", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('pos_adapter', 'manual', 'file_import', 'api', 'inventoryos_pos')",
            name="ck_sales_sources_kind",
        ),
        sa.CheckConstraint(
            "active_to IS NULL OR active_to >= active_from", name="ck_sales_sources_span"
        ),
        _fk("sales_sources", "tenant_id", "tenants.id"),
        _fk("sales_sources", "location_id", "locations.id"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sales_sources") as batch:
        batch.create_index("ix_sales_sources_location", ["location_id", "active_from"])
        batch.create_index("ix_sales_sources_tenant_id", ["tenant_id"])

    op.create_table(
        "sales_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("sales_source_id", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("source_reference", sa.String(length=200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("of_record", sa.Boolean(), nullable=False),
        sa.Column("gross_cents", sa.Integer(), nullable=True),
        sa.Column("discount_cents", sa.Integer(), nullable=True),
        sa.Column("net_cents", sa.Integer(), nullable=True),
        sa.Column("tax_cents", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "origin IN ('pos_adapter', 'manual', 'file_import', 'api', 'inventoryos_pos')",
            name="ck_sales_transactions_origin",
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'corrected', 'voided')", name="ck_sales_transactions_status"
        ),
        sa.CheckConstraint(
            "CAST(revision AS NUMERIC) > 0", name="ck_sales_transactions_revision_positive"
        ),
        _fk("sales_transactions", "tenant_id", "tenants.id"),
        _fk("sales_transactions", "location_id", "locations.id"),
        _fk("sales_transactions", "sales_source_id", "sales_sources.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sales_source_id", "source_reference", "revision", name="uq_sales_transactions_ref"
        ),
    )
    with op.batch_alter_table("sales_transactions") as batch:
        batch.create_index("ix_sales_transactions_day", ["location_id", "business_date"])
        batch.create_index("ix_sales_transactions_sales_source_id", ["sales_source_id"])
        batch.create_index("ix_sales_transactions_tenant_id", ["tenant_id"])

    op.create_table(
        "sales_transaction_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("sales_transaction_id", sa.Integer(), nullable=False),
        sa.Column("line_key", sa.String(length=120), nullable=False),
        sa.Column("product_variation_id", sa.Integer(), nullable=True),
        sa.Column("external_ref", sa.String(length=160), nullable=True),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("quantity", Qty(), nullable=False),
        sa.Column("gross_cents", sa.Integer(), nullable=True),
        sa.Column("net_cents", sa.Integer(), nullable=True),
        sa.Column("consumption_status", sa.String(length=8), nullable=False),
        sa.Column("recipe_version_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "CAST(quantity AS NUMERIC) >= 0",
            name="ck_sales_transaction_lines_quantity_not_negative",
        ),
        sa.CheckConstraint(
            "consumption_status IN ('pending', 'posted', 'gap', 'skipped')",
            name="ck_sales_transaction_lines_consumption_status",
        ),
        sa.CheckConstraint(
            "product_variation_id IS NOT NULL OR external_ref IS NOT NULL",
            name="ck_sales_transaction_lines_what",
        ),
        _fk("sales_transaction_lines", "tenant_id", "tenants.id"),
        _fk("sales_transaction_lines", "sales_transaction_id", "sales_transactions.id", "CASCADE"),
        _fk("sales_transaction_lines", "product_variation_id", "product_variations.id"),
        _fk("sales_transaction_lines", "recipe_version_id", "recipe_versions.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sales_transaction_id", "line_key", name="uq_sales_transaction_lines_key"
        ),
    )
    with op.batch_alter_table("sales_transaction_lines") as batch:
        batch.create_index(
            "ix_sales_transaction_lines_product_variation_id", ["product_variation_id"]
        )
        batch.create_index(
            "ix_sales_transaction_lines_sales_transaction_id", ["sales_transaction_id"]
        )

    op.create_table(
        "sales_line_modifiers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("sales_transaction_id", sa.Integer(), nullable=False),
        sa.Column("sales_transaction_line_id", sa.Integer(), nullable=False),
        sa.Column("modifier_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "CAST(quantity AS NUMERIC) > 0", name="ck_sales_line_modifiers_quantity_positive"
        ),
        _fk("sales_line_modifiers", "tenant_id", "tenants.id"),
        _fk("sales_line_modifiers", "sales_transaction_id", "sales_transactions.id", "CASCADE"),
        _fk(
            "sales_line_modifiers",
            "sales_transaction_line_id",
            "sales_transaction_lines.id",
            "CASCADE",
        ),
        _fk("sales_line_modifiers", "modifier_id", "modifiers.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sales_transaction_line_id", "modifier_id", name="uq_sales_line_modifiers_modifier"
        ),
    )
    with op.batch_alter_table("sales_line_modifiers") as batch:
        batch.create_index("ix_sales_line_modifiers_sales_transaction_id", ["sales_transaction_id"])
        batch.create_index(
            "ix_sales_line_modifiers_sales_transaction_line_id", ["sales_transaction_line_id"]
        )

    op.create_table(
        "sales_returns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("sales_source_id", sa.Integer(), nullable=False),
        sa.Column("sales_transaction_id", sa.Integer(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("source_reference", sa.String(length=200), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("classification", sa.String(length=8), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "origin IN ('pos_adapter', 'manual', 'file_import', 'api', 'inventoryos_pos')",
            name="ck_sales_returns_origin",
        ),
        sa.CheckConstraint("status IN ('recorded', 'classified')", name="ck_sales_returns_status"),
        sa.CheckConstraint(
            "classification IS NULL OR classification IN ('restock', 'waste')",
            name="ck_sales_returns_classification",
        ),
        _fk("sales_returns", "tenant_id", "tenants.id"),
        _fk("sales_returns", "location_id", "locations.id"),
        _fk("sales_returns", "sales_source_id", "sales_sources.id"),
        _fk("sales_returns", "sales_transaction_id", "sales_transactions.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sales_source_id", "source_reference", name="uq_sales_returns_ref"),
    )
    with op.batch_alter_table("sales_returns") as batch:
        batch.create_index("ix_sales_returns_sales_source_id", ["sales_source_id"])
        batch.create_index("ix_sales_returns_tenant_id", ["tenant_id"])

    op.create_table(
        "sales_return_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("sales_return_id", sa.Integer(), nullable=False),
        sa.Column("sales_transaction_line_id", sa.Integer(), nullable=True),
        sa.Column("quantity", Qty(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "CAST(quantity AS NUMERIC) > 0", name="ck_sales_return_lines_quantity_positive"
        ),
        _fk("sales_return_lines", "tenant_id", "tenants.id"),
        _fk("sales_return_lines", "sales_return_id", "sales_returns.id", "CASCADE"),
        _fk("sales_return_lines", "sales_transaction_line_id", "sales_transaction_lines.id"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sales_return_lines") as batch:
        batch.create_index("ix_sales_return_lines_sales_return_id", ["sales_return_id"])

    backfill(op.get_bind())


def downgrade() -> None:
    for table in (
        "sales_return_lines",
        "sales_returns",
        "sales_line_modifiers",
        "sales_transaction_lines",
        "sales_transactions",
        "sales_sources",
    ):
        op.drop_table(table)
