from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.function_branch import FunctionBranch
from app.repositories.lineage_repo import LineageRepository
from app.repositories.tenant_repo import TenantRepository
from app.repositories.user_repo import UserRepository

router = APIRouter()


@router.get("/profile/{handle}")
async def public_profile(
    handle: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    tenant_repo = TenantRepository(session)
    tenant = await tenant_repo.get_by_id(handle)
    if not tenant:
        raise HTTPException(status_code=404, detail="Not found")

    lineage_repo = LineageRepository(session)
    repos = await lineage_repo.list_repo_branches(handle)

    # Function counts per (repo, branch)
    count_result = await session.execute(
        select(
            FunctionBranch.repo,
            FunctionBranch.branch,
            func.count(FunctionBranch.asset_id).label("cnt"),
        )
        .where(FunctionBranch.tenant_id == handle)
        .group_by(FunctionBranch.repo, FunctionBranch.branch)
    )
    func_counts = {(r, b): c for r, b, c in count_result.all()}

    # Enrich repos with function counts
    enriched_repos = [
        {**r, "function_count": func_counts.get((r["repo"], r["branch"]), 0)}
        for r in repos
    ]

    # Check if requester owns this profile
    is_owner = False
    users: list = []
    user_id = request.session.get("user_id")
    if user_id:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_id(int(user_id))
        if user and user.tenant_id == handle:
            is_owner = True
            tenant_users = await user_repo.list_for_tenant(handle)
            users = [
                {"id": u.id, "username": u.username, "is_active": u.is_active}
                for u in tenant_users
            ]

    unique_repos = len({r["repo"] for r in repos})
    total_functions = sum(func_counts.values())

    return {
        "tenant": {
            "tenant_id": tenant.tenant_id,
            "tenant_name": tenant.tenant_name,
            "account_type": tenant.account_type,
        },
        "repos": enriched_repos,
        "stats": {
            "total_repos": unique_repos,
            "total_functions": total_functions,
            "member_count": len(users) if is_owner else None,
        },
        "users": users,
        "is_owner": is_owner,
    }
