from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invite_token import InviteToken


class InviteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def is_valid(self, invite: InviteToken) -> bool:
        now = datetime.now(timezone.utc)
        if invite.expires_at and invite.expires_at < now:
            return False
        if invite.used_count >= invite.max_uses:
            return False
        return True

    async def create(
        self,
        tenant_id: str,
        created_by_user_id: int,
        max_uses: int,
        expires_at: datetime | None,
    ) -> InviteToken:
        invite = InviteToken(
            token=secrets.token_urlsafe(24),
            tenant_id=tenant_id,
            created_by_user_id=created_by_user_id,
            max_uses=max_uses,
            used_count=0,
            expires_at=expires_at,
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

    async def list_for_tenant(self, tenant_id: str) -> list[InviteToken]:
        result = await self._s.execute(
            select(InviteToken)
            .where(InviteToken.tenant_id == tenant_id)
            .options(selectinload(InviteToken.created_by))
            .order_by(InviteToken.created_at.desc())
        )
        return list(result.scalars().all())

    async def delete(self, invite: InviteToken) -> None:
        await self._s.delete(invite)
        await self._s.flush()

    async def increment_used(self, invite: InviteToken) -> None:
        invite.used_count += 1
        await self._s.flush()
