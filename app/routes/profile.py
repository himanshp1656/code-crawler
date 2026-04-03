from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories.lineage_repo import LineageRepository
from app.repositories.tenant_repo import TenantRepository

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/{handle}", response_class=HTMLResponse)
async def public_profile(
    request: Request,
    handle: str,
    session: AsyncSession = Depends(get_session),
):
    tenant_repo = TenantRepository(session)
    tenant = await tenant_repo.get_by_id(handle)
    if not tenant:
        raise HTTPException(status_code=404, detail="Not found")

    lineage_repo = LineageRepository(session)
    repos = await lineage_repo.list_repo_branches(handle)

    return templates.TemplateResponse(
        request, "profile.html", {"tenant": tenant, "repos": repos}
    )
