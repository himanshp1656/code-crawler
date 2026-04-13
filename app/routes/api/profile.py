from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories.lineage_repo import LineageRepository
from app.repositories.tenant_repo import TenantRepository

router = APIRouter()


@router.get("/profile/{handle}")
async def public_profile(
    handle: str,
    session: AsyncSession = Depends(get_session),
):
    tenant_repo = TenantRepository(session)
    tenant = await tenant_repo.get_by_id(handle)
    if not tenant:
        raise HTTPException(status_code=404, detail="Not found")
    lineage_repo = LineageRepository(session)
    repos = await lineage_repo.list_repo_branches(handle)
    return {
        "tenant": {
            "tenant_id": tenant.tenant_id,
            "display_name": tenant.display_name,
            "account_type": tenant.account_type,
        },
        "repos": repos,
    }
