from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_id(self, tenant_id: str) -> Optional[Tenant]:
        return await self._s.get(Tenant, tenant_id)

    async def list_all(self) -> List[Tenant]:
        result = await self._s.execute(
            select(Tenant).order_by(Tenant.created_at.desc())
        )
        return list(result.scalars().all())

    async def exists(self, tenant_id: str) -> bool:
        result = await self._s.execute(
            select(Tenant.tenant_id).where(Tenant.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none() is not None

    async def create(
        self,
        tenant_id: str,
        tenant_name: str,
        account_type: str = "personal",
    ) -> Tenant:
        tenant = Tenant(
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            account_type=account_type,
        )
        self._s.add(tenant)
        await self._s.flush()
        return tenant
