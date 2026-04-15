"""add github_pat_encrypted to users

Revision ID: 015
Revises: 014
Create Date: 2026-04-15
"""
from alembic import op
import sqlalchemy as sa

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("github_pat_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "github_pat_encrypted")
