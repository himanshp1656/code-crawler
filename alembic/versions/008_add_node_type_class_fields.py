"""add node_type class_id base_class_ids relationships to lineage_nodes

Revision ID: 008
Revises: 007
Create Date: 2026-04-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lineage_nodes", sa.Column(
        "node_type", sa.String(), nullable=False, server_default="function"
    ))
    op.add_column("lineage_nodes", sa.Column(
        "class_id", sa.String(), nullable=True
    ))
    op.add_column("lineage_nodes", sa.Column(
        "base_class_ids",
        postgresql.JSONB(),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ))
    op.add_column("lineage_nodes", sa.Column(
        "relationships",
        postgresql.JSONB(),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ))
    # fast lookup: "give me all methods of class X"
    op.create_index(
        "ix_lineage_nodes_class_id",
        "lineage_nodes",
        ["tenant_id", "repo", "branch", "class_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_lineage_nodes_class_id", "lineage_nodes")
    op.drop_column("lineage_nodes", "relationships")
    op.drop_column("lineage_nodes", "base_class_ids")
    op.drop_column("lineage_nodes", "class_id")
    op.drop_column("lineage_nodes", "node_type")
