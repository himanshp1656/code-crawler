"""add source and end_lineno to lineage_nodes

Revision ID: 003
Revises: 002
Create Date: 2026-04-03
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lineage_nodes", sa.Column("end_lineno", sa.Integer(), nullable=True))
    op.add_column("lineage_nodes", sa.Column("source", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("lineage_nodes", "source")
    op.drop_column("lineage_nodes", "end_lineno")
