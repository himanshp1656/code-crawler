from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List

from sqlalchemy import String, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .lineage_node import LineageNode
    from .user import User


class Tenant(Base):
    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_name: Mapped[str] = mapped_column(String, nullable=False)
    account_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default="personal",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
    )

    users: Mapped[List[User]] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
    )
    lineage_nodes: Mapped[List[LineageNode]] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
    )
