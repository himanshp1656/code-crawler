"""rename upstream_ids to downstream_ids

Revision ID: 007
Revises: 006
Create Date: 2026-04-10
"""
from typing import Sequence, Union

from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("lineage_nodes", "upstream_ids", new_column_name="downstream_ids")


def downgrade() -> None:
    op.alter_column("lineage_nodes", "downstream_ids", new_column_name="upstream_ids")
