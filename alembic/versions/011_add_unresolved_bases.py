"""add unresolved_bases to lineage_nodes

Revision ID: 011
Revises: 010
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lineage_nodes",
        sa.Column("unresolved_bases", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("lineage_nodes", "unresolved_bases")
