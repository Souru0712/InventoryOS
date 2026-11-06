"""Locations without a POS (DM-3): address, cutoff, open days, archive; business profile.

Additive. A location no longer needs provider ids, so a business without Square can add one.

Revision ID: 912d17673f03
Revises: 68c6fdbc31a7
Create Date: 2026-11-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "912d17673f03"
down_revision = "68c6fdbc31a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("address", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("contact_email", sa.String(length=255), nullable=True))

    with op.batch_alter_table("locations") as batch:
        batch.add_column(sa.Column("address", sa.String(length=300), nullable=True))
        batch.add_column(
            sa.Column(
                "business_day_cutoff_hour",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(
            sa.Column("open_days", sa.Integer(), nullable=False, server_default=sa.text("127"))
        )
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.alter_column("provider", existing_type=sa.String(length=32), nullable=True)
        batch.alter_column(
            "provider_location_id", existing_type=sa.String(length=120), nullable=True
        )
        batch.create_check_constraint(
            "ck_locations_business_day_cutoff_hour", "business_day_cutoff_hour BETWEEN 0 AND 23"
        )
        batch.create_check_constraint("ck_locations_open_days", "open_days BETWEEN 0 AND 127")


def downgrade() -> None:
    # The older schema needs provider ids: give owner-added locations ones that stay unique.
    op.execute(
        "UPDATE locations SET provider = 'manual', "
        "provider_location_id = 'manual-' || CAST(id AS VARCHAR(20)) "
        "WHERE provider IS NULL OR provider_location_id IS NULL"
    )
    with op.batch_alter_table("locations") as batch:
        batch.drop_constraint("ck_locations_open_days", type_="check")
        batch.drop_constraint("ck_locations_business_day_cutoff_hour", type_="check")
        batch.alter_column(
            "provider_location_id", existing_type=sa.String(length=120), nullable=False
        )
        batch.alter_column("provider", existing_type=sa.String(length=32), nullable=False)
        batch.drop_column("archived_at")
        batch.drop_column("open_days")
        batch.drop_column("business_day_cutoff_hour")
        batch.drop_column("address")

    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("contact_email")
        batch.drop_column("address")
