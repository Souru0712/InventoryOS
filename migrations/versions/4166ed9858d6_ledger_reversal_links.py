"""Ledger reversal links (T1.16-b): a reversal names what it undoes, and only a reversal does.

Additive constraints. Nothing has posted to the ledger yet (T1.17 is the first writer), so no
existing row can break them.

Revision ID: 4166ed9858d6
Revises: 632386e0eb3f
Create Date: 2026-11-12
"""

from __future__ import annotations

from alembic import op

revision = "4166ed9858d6"
down_revision = "632386e0eb3f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("inventory_transactions") as batch:
        batch.create_check_constraint(
            "ck_inventory_transactions_reversal_link",
            "(kind = 'reversal' AND reverses_transaction_id IS NOT NULL)"
            " OR (kind <> 'reversal' AND reverses_transaction_id IS NULL)",
        )
    with op.batch_alter_table("inventory_movements") as batch:
        batch.create_check_constraint(
            "ck_inventory_movements_reversal_link",
            "(movement_type = 'reversal' AND reverses_movement_id IS NOT NULL)"
            " OR (movement_type <> 'reversal' AND reverses_movement_id IS NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("inventory_movements") as batch:
        batch.drop_constraint("ck_inventory_movements_reversal_link", type_="check")
    with op.batch_alter_table("inventory_transactions") as batch:
        batch.drop_constraint("ck_inventory_transactions_reversal_link", type_="check")
