"""add repo_settings table

Revision ID: 010
Revises: 009
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repo_settings",
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("default_branch", sa.String(), nullable=False, server_default="main"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "repo", name="pk_repo_settings"),
    )


def downgrade() -> None:
    op.drop_table("repo_settings")
