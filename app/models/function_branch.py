from __future__ import annotations

from datetime import datetime
from typing import Any, List

from sqlalchemy import ForeignKey, Index, PrimaryKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class FunctionBranch(Base):
    """
    One row per (tenant, repo, branch, function).
    Stores the branch-specific call edges for a function.
    Points to FunctionDef for the function's content/identity.
    """
    __tablename__ = "function_branches"

    tenant_id: Mapped[str] = mapped_column(String, nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    branch: Mapped[str] = mapped_column(String, nullable=False)
    asset_id: Mapped[str] = mapped_column(String, nullable=False)

    # FK to function_defs — which version of this function is in this branch
    def_id: Mapped[str] = mapped_column(
        ForeignKey("function_defs.id", ondelete="CASCADE"),
        nullable=False,
    )

    repo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    workflow_id: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str] = mapped_column(String, nullable=False)

    # Branch-specific call edges
    upstream_ids: Mapped[List[Any]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )
    downstream_ids: Mapped[List[Any]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )

    # For class nodes: resolved parent class IDs (branch-specific, resolved at build time)
    base_class_ids: Mapped[List[Any]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )

    relationships: Mapped[List[Any]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )

    # Class nodes: unresolved base class names (cleared after cross-repo resolution)
    unresolved_bases: Mapped[List[Any]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "repo", "branch", "asset_id", name="pk_function_branches"),
        Index("ix_fb_tenant_repo_branch", "tenant_id", "repo", "branch"),
        Index("ix_fb_def_id", "def_id"),
    )
