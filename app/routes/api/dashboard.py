from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.crawl_job import CrawlJob
from app.models.repo_settings import RepoSettings
from app.repositories.lineage_repo import LineageRepository, normalize_repo_name
from app.repositories.tenant_repo import TenantRepository
from app.repositories.user_repo import UserRepository
from app.routes.api.workflows import insert_crawl_job
from app.workflow import TASK_QUEUE, CodeCrawlerWorkflow

router = APIRouter()


async def _get_user(request: Request, session: AsyncSession):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="User inactive or not found")
    return user


class CrawlRequest(BaseModel):
    github_repo_url: str
    branch: str = "main"


class DefaultBranchRequest(BaseModel):
    repo: str
    branch: str


@router.get("/dashboard")
async def dashboard_data(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    tenant_repo = TenantRepository(session)
    tenant = await tenant_repo.get_by_id(user.tenant_id)
    user_repo = UserRepository(session)
    tenant_users = await user_repo.list_for_tenant(user.tenant_id)
    lineage_repo = LineageRepository(session)
    repo_branches = await lineage_repo.list_repo_branches(user.tenant_id)
    repo_map: dict = defaultdict(list)
    repo_url_map: dict = {}
    repo_default_branch: dict = {}
    for x in repo_branches:
        repo_map[x["repo"]].append(x["branch"])
        if x.get("repo_url"):
            repo_url_map[x["repo"]] = x["repo_url"]
        if x.get("default_branch"):
            repo_default_branch[x["repo"]] = x["default_branch"]
    repos = [
        {
            "repo": repo,
            "branches": branches,
            "repo_url": repo_url_map.get(repo),
            "default_branch": repo_default_branch.get(repo, ""),
        }
        for repo, branches in repo_map.items()
    ]
    return {
        "tenant_id": user.tenant_id,
        "tenant_name": tenant.tenant_name if tenant else user.tenant_id,
        "account_type": tenant.account_type if tenant else "personal",
        "repos": repos,
        "users": [
            {"id": u.id, "username": u.username, "is_active": u.is_active}
            for u in tenant_users
        ],
    }


@router.get("/lineage/classes")
async def class_lineage_data(
    request: Request,
    repo: str,
    branch: str = "main",
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    lineage_repo = LineageRepository(session)
    return await lineage_repo.fetch_class_lineage_data(
        user.tenant_id, normalize_repo_name(repo), branch
    )


@router.post("/repos/default-branch")
async def set_default_branch(
    body: DefaultBranchRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    lineage_repo = LineageRepository(session)
    await lineage_repo.set_default_branch(user.tenant_id, body.repo, body.branch)
    return {"ok": True}


@router.post("/crawl")
async def crawl(
    body: CrawlRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    client = request.app.state.temporal_client
    assert client is not None, "Temporal client not initialised"
    wf_id = f"code-crawler-{user.tenant_id}-{body.branch}-{body.github_repo_url.rsplit('/', 1)[-1]}"
    handle = await client.start_workflow(
        CodeCrawlerWorkflow.run,
        args=[body.github_repo_url, body.branch, "python", None, user.tenant_id, None, str(user.id)],
        id=wf_id,
        task_queue=TASK_QUEUE,
    )
    repo_name = normalize_repo_name(body.github_repo_url)
    await insert_crawl_job(session, tenant_id=user.tenant_id, user_id=user.id,
        workflow_id=handle.id, repo=repo_name, branch=body.branch, triggered_by=user.username)
    await session.commit()
    return {"workflow_id": handle.id, "status": "started"}


@router.get("/branch-functions")
async def branch_functions(
    request: Request,
    repo: str,
    branch: str,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    return await lineage_repo.list_functions_for_branch(user.tenant_id, safe_repo, branch)


@router.get("/function-source")
async def function_source(
    request: Request,
    repo: str,
    branch: str,
    name: str,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    node = await lineage_repo.fetch_node_by_name(user.tenant_id, safe_repo, branch, name)
    if not node:
        raise HTTPException(status_code=404, detail="Function not found")
    return node


@router.post("/crawl-local")
async def crawl_local(
    request: Request,
    folder_name: str = Form(...),
    branch: str = Form("local"),
    files: List[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)

    # Save uploaded files into output/repos/{folder}-{branch}/ — same location
    # clone_repo_activity uses, so run-in-repo can find and execute them.
    safe_name = folder_name.replace(" ", "-").lower()
    repo_dir = Path("output") / "repos" / f"{safe_name}-{branch}"
    repo_dir.mkdir(parents=True, exist_ok=True)
    for upload in files:
        rel_path = upload.filename or upload.filename
        dest = repo_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(await upload.read())

    client = request.app.state.temporal_client
    assert client is not None, "Temporal client not initialised"
    wf_id = f"code-crawler-{user.tenant_id}-{branch}-{safe_name}"
    handle = await client.start_workflow(
        CodeCrawlerWorkflow.run,
        args=[safe_name, branch, "python", None, user.tenant_id, str(repo_dir.resolve()), str(user.id)],
        id=wf_id,
        task_queue=TASK_QUEUE,
    )
    await insert_crawl_job(session, tenant_id=user.tenant_id, user_id=user.id,
        workflow_id=handle.id, repo=safe_name, branch=branch, triggered_by=user.username)
    await session.commit()
    return {"workflow_id": handle.id, "status": "started"}


@router.delete("/repos/branch")
async def delete_branch(
    repo: str,
    branch: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    deleted = await lineage_repo.delete_branch(user.tenant_id, safe_repo, branch)
    # Remove crawl job history for this branch
    await session.execute(
        sql_delete(CrawlJob).where(
            CrawlJob.tenant_id == user.tenant_id,
            CrawlJob.repo == safe_repo,
            CrawlJob.branch == branch,
        )
    )
    # If no branches remain for this repo, clean up repo_settings too
    remaining = await lineage_repo.list_branches_for_repo(user.tenant_id, safe_repo)
    if not remaining:
        await session.execute(
            sql_delete(RepoSettings).where(
                RepoSettings.tenant_id == user.tenant_id,
                RepoSettings.repo == safe_repo,
            )
        )
    await session.commit()
    return {"ok": True, "deleted": deleted}


@router.delete("/repos")
async def delete_repo(
    repo: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    deleted = await lineage_repo.delete_repo(user.tenant_id, safe_repo)
    await session.execute(
        sql_delete(CrawlJob).where(
            CrawlJob.tenant_id == user.tenant_id,
            CrawlJob.repo == safe_repo,
        )
    )
    await session.execute(
        sql_delete(RepoSettings).where(
            RepoSettings.tenant_id == user.tenant_id,
            RepoSettings.repo == safe_repo,
        )
    )
    await session.commit()
    return {"ok": True, "deleted": deleted}
