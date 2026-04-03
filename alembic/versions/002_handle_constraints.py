"""add handle constraints to tenants

Revision ID: 002
Revises: 001
Create Date: 2026-04-03
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_tenants_handle_format",
        "tenants",
        sa.text(
            "tenant_id ~ '^[a-z0-9]([a-z0-9-]{1,37}[a-z0-9])?$'"
            " OR tenant_id = 'default'"  # allow legacy seed tenant
        ),
    )
    op.create_check_constraint(
        "ck_tenants_handle_not_reserved",
        "tenants",
        sa.text(
            "tenant_id NOT IN ("
            "'login','logout','signup','register','dashboard','admin',"
            "'ingest','api','static','assets','crawl','settings',"
            "'profile','help','about','pricing','docs','blog',"
            "'status','health','new','delete','edit','create',"
            "'explore','search','www','app','auth','oauth','default'"
            ")"
        ),
    )


def downgrade() -> None:
    op.drop_constraint("ck_tenants_handle_not_reserved", "tenants")
    op.drop_constraint("ck_tenants_handle_format", "tenants")
