from __future__ import annotations

from typing import List, Optional

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_id(self, user_id: int) -> Optional[User]:
        return await self._s.get(User, user_id)

    async def get_by_username(self, username: str) -> Optional[User]:
        result = await self._s.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def list_for_tenant(self, tenant_id: str) -> List[User]:
        result = await self._s.execute(
            select(User)
            .where(User.tenant_id == tenant_id)
            .order_by(User.created_at.desc())
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        tenant_id: str,
        username: str,
        password: str,
    ) -> User:
        user = User(
            tenant_id=tenant_id,
            username=username,
            password_hash=bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode(),
        )
        self._s.add(user)
        await self._s.flush()
        return user

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        return bcrypt.checkpw(plain.encode()[:72], hashed.encode())
