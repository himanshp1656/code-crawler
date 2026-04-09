"""composite pk for lineage_nodes

Revision ID: 005
Revises: 004
Create Date: 2026-04-06
"""
from typing import Sequence, Union
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old single-column PK, replace with composite PK
    op.execute("ALTER TABLE lineage_nodes DROP CONSTRAINT pk_lineage_nodes")
    op.create_primary_key(
        "pk_lineage_nodes",
        "lineage_nodes",
        ["asset_id", "tenant_id", "repo", "branch"],
    )


def downgrade() -> None:
    op.execute("ALTER TABLE lineage_nodes DROP CONSTRAINT pk_lineage_nodes")
    op.create_primary_key("pk_lineage_nodes", "lineage_nodes", ["asset_id"])
