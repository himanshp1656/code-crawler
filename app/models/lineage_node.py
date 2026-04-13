from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, List

from sqlalchemy import ForeignKey, Index, Integer, PrimaryKeyConstraint, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .tenant import Tenant


class LineageNode(Base):
    __tablename__ = "lineage_nodes"

    asset_id: Mapped[str] = mapped_column(String, nullable=False)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    repo: Mapped[str] = mapped_column(String, nullable=False)
    branch: Mapped[str] = mapped_column(String, nullable=False)
    repo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    workflow_id: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str] = mapped_column(String, nullable=False)

    # "function" or "class"
    node_type: Mapped[str] = mapped_column(String, nullable=False, server_default="function")

    name: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    lineno: Mapped[int] = mapped_column(Integer, nullable=False)
    end_lineno: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    module_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # function nodes: ID of the enclosing class node (null for top-level functions)
    # class nodes: always null
    class_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # class nodes: resolved IDs of base classes  e.g. ["app.models.base:Base"]
    # function nodes: always []
    base_class_ids: Mapped[List[Any]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )

    # call edges: callers of this node (downstream consumers)
    downstream_ids: Mapped[List[Any]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )

    # call edges: callees of this node (functions this node calls)
    upstream_ids: Mapped[List[Any]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )

    # other typed relationships e.g. [{"type": "instantiates", "target_id": "..."}]
    relationships: Mapped[List[Any]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )

    # class nodes: base class names that couldn't be resolved within this repo
    # e.g. [{"name": "SqlMetadataExtractor", "qualified_key": "application_sdk.templates.SqlMetadataExtractor"}]
    unresolved_bases: Mapped[List[Any]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
    )

    tenant: Mapped[Tenant] = relationship(back_populates="lineage_nodes")

    __table_args__ = (
        PrimaryKeyConstraint("asset_id", "tenant_id", "repo", "branch", name="pk_lineage_nodes"),
        Index("ix_lineage_nodes_tenant_repo_branch", "tenant_id", "repo", "branch"),
        Index("ix_lineage_nodes_class_id", "tenant_id", "repo", "branch", "class_id"),
    )
