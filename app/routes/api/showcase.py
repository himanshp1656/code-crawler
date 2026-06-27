"""Public read-only API for the demo/showcase tenant (no login required)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.function_branch import FunctionBranch
from app.repositories.lineage_repo import LineageRepository, normalize_repo_name
from app.repositories.tenant_repo import TenantRepository
from app.showcase_config import SHOWCASE_ENABLED, SHOWCASE_TENANT_ID, SHOWCASE_TENANT_NAME


async def _enforce_read_only(request: Request) -> None:
    if request.method != "GET":
        raise HTTPException(status_code=405, detail="Showcase is read-only")


router = APIRouter(dependencies=[Depends(_enforce_read_only)])


def _require_showcase() -> str:
    if not SHOWCASE_ENABLED:
        raise HTTPException(status_code=404, detail="Showcase is disabled")
    return SHOWCASE_TENANT_ID


@router.get("/showcase")
async def showcase_overview(
    tenant_id: str = Depends(_require_showcase),
    session: AsyncSession = Depends(get_session),
):
    """Landing data: tenant info + crawled repos with function counts."""
    tenant_repo = TenantRepository(session)
    tenant = await tenant_repo.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(status_code=503, detail="Showcase tenant not initialized")

    lineage_repo = LineageRepository(session)
    repos = await lineage_repo.list_repo_branches(tenant_id)

    count_result = await session.execute(
        select(
            FunctionBranch.repo,
            FunctionBranch.branch,
            func.count(FunctionBranch.asset_id).label("cnt"),
        )
        .where(FunctionBranch.tenant_id == tenant_id)
        .group_by(FunctionBranch.repo, FunctionBranch.branch)
    )
    func_counts = {(r, b): c for r, b, c in count_result.all()}

    enriched_repos = [
        {**r, "function_count": func_counts.get((r["repo"], r["branch"]), 0)}
        for r in repos
    ]

    unique_repos = len({r["repo"] for r in repos})
    total_functions = sum(func_counts.values())

    return {
        "tenant": {
            "tenant_id": tenant.tenant_id,
            "tenant_name": tenant.tenant_name,
            "account_type": tenant.account_type,
        },
        "display_name": SHOWCASE_TENANT_NAME,
        "repos": enriched_repos,
        "stats": {
            "total_repos": unique_repos,
            "total_functions": total_functions,
        },
        "read_only": True,
    }


@router.get("/showcase/lineage-stats")
async def showcase_lineage_stats(
    repo: str,
    branch: str = "main",
    tenant_id: str = Depends(_require_showcase),
    session: AsyncSession = Depends(get_session),
):
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    return await lineage_repo.fetch_lineage_stats(tenant_id, safe_repo, branch)


@router.get("/showcase/lineage-data")
async def showcase_lineage_data(
    repo: str,
    branch: str = "main",
    offset: int = 0,
    limit: int = 100,
    search: str = "",
    sort: str = "connections",
    tenant_id: str = Depends(_require_showcase),
    session: AsyncSession = Depends(get_session),
):
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    return await lineage_repo.fetch_lineage_data(
        tenant_id, safe_repo, branch,
        offset=offset, limit=limit, search=search, sort=sort,
    )


@router.get("/showcase/lineage-node")
async def showcase_lineage_node(
    repo: str,
    branch: str,
    asset_id: str,
    tenant_id: str = Depends(_require_showcase),
    session: AsyncSession = Depends(get_session),
):
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    data = await lineage_repo.fetch_node_with_neighbors(tenant_id, safe_repo, branch, asset_id)
    if not data:
        raise HTTPException(status_code=404, detail="Node not found")
    return data


@router.get("/showcase/lineage/classes")
async def showcase_class_lineage(
    repo: str,
    branch: str = "main",
    offset: int = 0,
    limit: int = 100,
    search: str = "",
    tenant_id: str = Depends(_require_showcase),
    session: AsyncSession = Depends(get_session),
):
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    return await lineage_repo.fetch_class_lineage_data(
        tenant_id, safe_repo, branch,
        offset=offset, limit=limit, search=search,
    )


@router.get("/showcase/branch-functions")
async def showcase_branch_functions(
    repo: str,
    branch: str,
    tenant_id: str = Depends(_require_showcase),
    session: AsyncSession = Depends(get_session),
):
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    return await lineage_repo.list_functions_for_branch(tenant_id, safe_repo, branch)


@router.get("/showcase/function-source")
async def showcase_function_source(
    repo: str,
    branch: str,
    name: str,
    tenant_id: str = Depends(_require_showcase),
    session: AsyncSession = Depends(get_session),
):
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    node = await lineage_repo.fetch_node_by_name(tenant_id, safe_repo, branch, name)
    if not node:
        raise HTTPException(status_code=404, detail="Function not found")
    return node
