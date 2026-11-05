"""documents store a storage key, not a local file path

Revision ID: b3d91f2a6c47
Revises: e7b19fdcb1ce
Create Date: 2026-09-15 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d91f2a6c47"
down_revision: str | None = "e7b19fdcb1ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.alter_column(
            "file_path",
            new_column_name="storage_key",
            existing_type=sa.String(length=500),
            existing_nullable=False,
        )

    # Uploads were written to <UPLOAD_DIR>/<tenant>/<sha256><ext>, so the key is always the last
    # two parts of the old path, whichever directory or separator it was saved under.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, storage_key FROM documents")).mappings().all()
    for row in rows:
        parts = [part for part in row["storage_key"].replace("\\", "/").split("/") if part]
        key = "/".join(parts[-2:])
        if key != row["storage_key"]:
            bind.execute(
                sa.text("UPDATE documents SET storage_key = :key WHERE id = :id"),
                {"key": key, "id": row["id"]},
            )


def downgrade() -> None:
    # Keys are left as they are: the old code expects a full path, so photos uploaded after
    # this revision will not be found by it, but no rows are lost.
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.alter_column(
            "storage_key",
            new_column_name="file_path",
            existing_type=sa.String(length=500),
            existing_nullable=False,
        )
