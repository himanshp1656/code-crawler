from __future__ import annotations

from typing import Optional

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_account import AdminAccount


class AdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_username(self, username: str) -> Optional[AdminAccount]:
        result = await self._s.execute(
            select(AdminAccount).where(AdminAccount.username == username)
        )
        return result.scalar_one_or_none()

    async def upsert(self, username: str, password: str) -> None:
        existing = await self.get_by_username(username)
        if existing:
            existing.password_hash = bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()
        else:
            self._s.add(
                AdminAccount(
                    username=username,
                    password_hash=bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode(),
                )
            )
        await self._s.flush()

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        return bcrypt.checkpw(plain.encode()[:72], hashed.encode())
