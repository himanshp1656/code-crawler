"""replace lineage_nodes + function_source with function_defs + function_branches

Revision ID: 019
Revises: 018
Create Date: 2026-04-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old tables (indexes dropped automatically)
    op.execute("DROP TABLE IF EXISTS function_source CASCADE")
    op.execute("DROP TABLE IF EXISTS lineage_nodes CASCADE")

    # Create function_defs
    op.create_table(
        "function_defs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String, sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("repo", sa.String, nullable=False),
        sa.Column("asset_id", sa.String, nullable=False),
        sa.Column("node_type", sa.String, nullable=False, server_default="function"),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("file_path", sa.String, nullable=False),
        sa.Column("lineno", sa.Integer, nullable=False),
        sa.Column("end_lineno", sa.Integer, nullable=True),
        sa.Column("source_hash", sa.String(32), nullable=False),
        sa.Column("source", sa.Text, nullable=True),
        sa.Column("module_context", JSONB, nullable=True),
        sa.Column("class_id", sa.String, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_function_defs"),
        sa.UniqueConstraint("tenant_id", "repo", "asset_id", "source_hash", name="uq_fd_version"),
    )
    op.create_index("ix_fd_tenant_repo_asset", "function_defs", ["tenant_id", "repo", "asset_id"])
    op.create_index("ix_fd_tenant_repo_node_type", "function_defs", ["tenant_id", "repo", "node_type"])
    op.create_index("ix_fd_name", "function_defs", ["tenant_id", "repo", "name"])
    op.execute("CREATE INDEX IF NOT EXISTS ix_fd_name_trgm ON function_defs USING gin (name gin_trgm_ops)")

    # Create function_branches
    op.create_table(
        "function_branches",
        sa.Column("tenant_id", sa.String, nullable=False),
        sa.Column("repo", sa.String, nullable=False),
        sa.Column("branch", sa.String, nullable=False),
        sa.Column("asset_id", sa.String, nullable=False),
        sa.Column("def_id", sa.String(32), sa.ForeignKey("function_defs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("repo_url", sa.String, nullable=True),
        sa.Column("workflow_id", sa.String, nullable=False),
        sa.Column("run_id", sa.String, nullable=False),
        sa.Column("upstream_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("downstream_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("base_class_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("relationships", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("unresolved_bases", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "repo", "branch", "asset_id", name="pk_function_branches"),
    )
    op.create_index("ix_fb_tenant_repo_branch", "function_branches", ["tenant_id", "repo", "branch"])
    op.create_index("ix_fb_def_id", "function_branches", ["def_id"])
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_fb_connected
        ON function_branches (tenant_id, repo, branch)
        WHERE jsonb_array_length(downstream_ids) > 0
           OR jsonb_array_length(upstream_ids) > 0
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_fb_downstream_gin ON function_branches USING gin (downstream_ids)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fb_upstream_gin ON function_branches USING gin (upstream_ids)")


def downgrade() -> None:
    op.drop_table("function_branches")
    op.drop_table("function_defs")
