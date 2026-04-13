"""add repo_url to lineage_nodes

Revision ID: 009
Revises: 008
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lineage_nodes", sa.Column("repo_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("lineage_nodes", "repo_url")
