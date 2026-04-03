from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, List

from sqlalchemy import ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .tenant import Tenant


class LineageNode(Base):
    __tablename__ = "lineage_nodes"

    asset_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    repo: Mapped[str] = mapped_column(String, nullable=False)
    branch: Mapped[str] = mapped_column(String, nullable=False)
    workflow_id: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    lineno: Mapped[int] = mapped_column(Integer, nullable=False)
    end_lineno: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    upstream_ids: Mapped[List[Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="'[]'::jsonb",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
    )

    tenant: Mapped[Tenant] = relationship(back_populates="lineage_nodes")

    __table_args__ = (
        Index(
            "ix_lineage_nodes_tenant_repo_branch",
            "tenant_id",
            "repo",
            "branch",
        ),
    )
