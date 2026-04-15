"""lineage_nodes performance indexes

Revision ID: 017
Revises: 016
Create Date: 2026-04-15
"""
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partial index for the default "connected" filter on the lineage page.
    # Most queries filter WHERE jsonb_array_length(downstream_ids)>0 OR ...upstream...
    # A partial index covering only those rows avoids scanning the full table.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ln_connected
        ON lineage_nodes (tenant_id, repo, branch)
        WHERE jsonb_array_length(downstream_ids) > 0
           OR jsonb_array_length(upstream_ids) > 0
    """)

    # node_type index — class-lineage and filter=class queries hit this frequently
    op.create_index(
        "ix_ln_node_type",
        "lineage_nodes",
        ["tenant_id", "repo", "branch", "node_type"],
    )

    # name index — for search (ILIKE '%q%' still needs seqscan, but prefix/exact
    # searches and ORDER BY name benefit from this)
    op.create_index(
        "ix_ln_name",
        "lineage_nodes",
        ["tenant_id", "repo", "branch", "name"],
    )

    # GIN indexes on the JSONB arrays.
    # The neighbor-lookup queries do  WHERE asset_id = ANY(:ids) which uses the PK,
    # but GIN helps for containment checks (@>) and jsonb_array_length on large arrays.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ln_downstream_gin
        ON lineage_nodes USING gin (downstream_ids)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ln_upstream_gin
        ON lineage_nodes USING gin (upstream_ids)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ln_connected")
    op.drop_index("ix_ln_node_type", "lineage_nodes")
    op.drop_index("ix_ln_name", "lineage_nodes")
    op.execute("DROP INDEX IF EXISTS ix_ln_downstream_gin")
    op.execute("DROP INDEX IF EXISTS ix_ln_upstream_gin")
