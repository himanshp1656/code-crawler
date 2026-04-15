from __future__ import annotations

from datetime import datetime
from typing import Any, List

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class FunctionDef(Base):
    """
    One row per unique (tenant, repo, function, source version).
    Shared across branches — if two branches have identical source for a function,
    they both point to the same FunctionDef row.
    """
    __tablename__ = "function_defs"

    # md5(tenant_id:repo:asset_id:source) — deterministic, content-addressed
    id: Mapped[str] = mapped_column(String(32), primary_key=True)

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    repo: Mapped[str] = mapped_column(String, nullable=False)

    # Stable identity key: "app/crawler.py:parse_repo" — does not include branch
    asset_id: Mapped[str] = mapped_column(String, nullable=False)

    node_type: Mapped[str] = mapped_column(String, nullable=False, server_default="function")
    name: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    lineno: Mapped[int] = mapped_column(Integer, nullable=False)
    end_lineno: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    module_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # For function nodes: ID of the enclosing class (null for top-level functions)
    class_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "repo", "asset_id", "source_hash", name="uq_fd_version"),
        Index("ix_fd_tenant_repo_asset", "tenant_id", "repo", "asset_id"),
        Index("ix_fd_tenant_repo_node_type", "tenant_id", "repo", "node_type"),
        Index("ix_fd_name", "tenant_id", "repo", "name"),
    )
