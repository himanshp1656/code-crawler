from __future__ import annotations

import secrets
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invite_token import InviteToken


class InviteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, tenant_id: str, created_by_user_id: int) -> InviteToken:
        token = secrets.token_urlsafe(24)
        invite = InviteToken(
            token=token,
            tenant_id=tenant_id,
            created_by_user_id=created_by_user_id,
        )
        self._s.add(invite)
        await self._s.flush()
        return invite

    async def get_by_token(self, token: str) -> Optional[InviteToken]:
        result = await self._s.execute(
            select(InviteToken)
            .where(InviteToken.token == token)
            .options(selectinload(InviteToken.tenant), selectinload(InviteToken.created_by))
        )
        return result.scalar_one_or_none()

    async def get_for_tenant(self, tenant_id: str) -> Optional[InviteToken]:
        """Return the existing invite token for a tenant if one exists."""
        result = await self._s.execute(
            select(InviteToken).where(InviteToken.tenant_id == tenant_id).limit(1)
        )
        return result.scalar_one_or_none()
