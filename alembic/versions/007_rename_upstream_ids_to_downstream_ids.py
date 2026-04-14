"""rename upstream_ids to downstream_ids

Revision ID: 007
Revises: 006
Create Date: 2026-04-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Column was already created as downstream_ids in migration 001 for fresh installs.
    # Only rename if upstream_ids still exists (legacy databases).
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='lineage_nodes' AND column_name='upstream_ids'"
        )
    )
    if result.fetchone():
        op.alter_column("lineage_nodes", "upstream_ids", new_column_name="downstream_ids")


def downgrade() -> None:
    pass
