from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.test_case import FunctionTestCase


class TestCaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list(
        self, tenant_id: str, repo: str, function_name: str
    ) -> List[FunctionTestCase]:
        result = await self._s.execute(
            select(FunctionTestCase)
            .where(
                FunctionTestCase.tenant_id == tenant_id,
                FunctionTestCase.repo == repo,
                FunctionTestCase.function_name == function_name,
            )
            .order_by(FunctionTestCase.created_at)
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        tenant_id: str,
        repo: str,
        function_name: str,
        label: str,
        args: Dict[str, Any],
        expected: Optional[Any] = None,
    ) -> FunctionTestCase:
        tc = FunctionTestCase(
            tenant_id=tenant_id,
            repo=repo,
            function_name=function_name,
            label=label,
            args=args,
            expected=expected,
        )
        self._s.add(tc)
        await self._s.commit()
        await self._s.refresh(tc)
        return tc

    async def update_expected(self, tenant_id: str, tc_id: int, expected: Any) -> bool:
        result = await self._s.execute(
            update(FunctionTestCase)
            .where(
                FunctionTestCase.id == tc_id,
                FunctionTestCase.tenant_id == tenant_id,
            )
            .values(expected=expected)
        )
        await self._s.commit()
        return result.rowcount > 0

    async def delete(self, tenant_id: str, tc_id: int) -> bool:
        result = await self._s.execute(
            delete(FunctionTestCase).where(
                FunctionTestCase.id == tc_id,
                FunctionTestCase.tenant_id == tenant_id,
            )
        )
        await self._s.commit()
        return result.rowcount > 0
