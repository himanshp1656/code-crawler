"""split function source into separate table; add trigram index for name search

Revision ID: 018
Revises: 017
Create Date: 2026-04-15
"""
from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create function_source table
    op.create_table(
        "function_source",
        sa.Column("asset_id", sa.String, nullable=False),
        sa.Column("tenant_id", sa.String, nullable=False),
        sa.Column("repo", sa.String, nullable=False),
        sa.Column("branch", sa.String, nullable=False),
        sa.Column("source", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("asset_id", "tenant_id", "repo", "branch", name="pk_function_source"),
    )

    # 2. Migrate existing source data before dropping the column
    op.execute("""
        INSERT INTO function_source (asset_id, tenant_id, repo, branch, source)
        SELECT asset_id, tenant_id, repo, branch, source
        FROM lineage_nodes
        WHERE source IS NOT NULL
    """)

    # 3. Drop source column from lineage_nodes
    op.drop_column("lineage_nodes", "source")

    # 4. Enable pg_trgm and add trigram index for ILIKE name search
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ln_name_trgm
        ON lineage_nodes USING gin (name gin_trgm_ops)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ln_name_trgm")
    op.add_column("lineage_nodes", sa.Column("source", sa.Text, nullable=True))
    op.execute("""
        UPDATE lineage_nodes ln
        SET source = fs.source
        FROM function_source fs
        WHERE ln.asset_id = fs.asset_id
          AND ln.tenant_id = fs.tenant_id
          AND ln.repo = fs.repo
          AND ln.branch = fs.branch
    """)
    op.drop_table("function_source")
