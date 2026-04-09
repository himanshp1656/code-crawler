"""add module_context to lineage_nodes

Revision ID: 004
Revises: 003
Create Date: 2026-04-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lineage_nodes", sa.Column("module_context", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("lineage_nodes", "module_context")
