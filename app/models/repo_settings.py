from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class RepoSettings(Base):
    __tablename__ = "repo_settings"

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    repo: Mapped[str] = mapped_column(String, primary_key=True, nullable=False)
    default_branch: Mapped[str] = mapped_column(String, nullable=False, default="main")
