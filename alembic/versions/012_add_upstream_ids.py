"""add upstream_ids to lineage_nodes

Revision ID: 012
Revises: 011
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lineage_nodes",
        sa.Column("upstream_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("lineage_nodes", "upstream_ids")
