"""create function_test_cases

Revision ID: 006
Revises: 005
Create Date: 2026-04-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "function_test_cases",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.String,
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("repo", sa.String, nullable=False),
        sa.Column("function_name", sa.String, nullable=False),
        sa.Column("label", sa.String, nullable=False),
        sa.Column("args", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expected", JSONB, nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_test_cases_tenant_repo_fn",
        "function_test_cases",
        ["tenant_id", "repo", "function_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_test_cases_tenant_repo_fn", table_name="function_test_cases")
    op.drop_table("function_test_cases")
