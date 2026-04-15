"""add max_uses, used_count, expires_at to invite_tokens

Revision ID: 014
Revises: 013
Create Date: 2026-04-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invite_tokens", sa.Column("max_uses", sa.Integer(), nullable=False, server_default="5"))
    op.add_column("invite_tokens", sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("invite_tokens", sa.Column("expires_at", TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("invite_tokens", "expires_at")
    op.drop_column("invite_tokens", "used_count")
    op.drop_column("invite_tokens", "max_uses")
