"""add crawl_jobs table

Revision ID: 016
Revises: 015
Create Date: 2026-04-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("branch", sa.String(), nullable=False),
        sa.Column("triggered_by", sa.String(), nullable=True),
        sa.Column("started_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_crawl_jobs"),
        sa.UniqueConstraint("workflow_id", name="uq_crawl_jobs_workflow_id"),
    )
    op.create_index("ix_crawl_jobs_tenant_id", "crawl_jobs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_crawl_jobs_tenant_id", "crawl_jobs")
    op.drop_table("crawl_jobs")
