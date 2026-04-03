from __future__ import annotations

from typing import Any, Dict
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from app.db import get_session
from app.repositories.lineage_repo import LineageRepository, normalize_repo_name
from app.repositories.user_repo import UserRepository
from app.workflow import TASK_QUEUE, CodeCrawlerWorkflow

router = APIRouter()
templates = Jinja2Templates(directory="templates")


async def _get_user(request: Request, session: AsyncSession) -> Dict[str, Any]:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="User inactive or not found")
    return user


def _temporal(request: Request) -> Client:
    client = request.app.state.temporal_client
    assert client is not None, "Temporal client not initialised"
    return client


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    lineage_repo = LineageRepository(session)
    repo_branches = await lineage_repo.list_repo_branches(user.tenant_id)
    lineages = [
        {
            "repo_q": quote(x["repo"], safe=""),
            "branch_q": quote(x["branch"], safe=""),
            "repo": x["repo"],
            "branch": x["branch"],
        }
        for x in repo_branches
    ]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"tenant_id": user.tenant_id, "lineages": lineages},
    )


@router.post("/crawl")
async def crawl(
    request: Request,
    github_repo_url: str = Form(...),
    branch: str = Form("main"),
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    client = _temporal(request)

    handle = await client.start_workflow(
        CodeCrawlerWorkflow.run,
        args=[github_repo_url, branch, "python", None, user.tenant_id],
        id=f"code-crawler-{user.tenant_id}-{branch}-{github_repo_url.rsplit('/', 1)[-1]}",
        task_queue=TASK_QUEUE,
    )
    _ = handle

    return RedirectResponse(
        url=f"/lineage-ui?repo={quote(github_repo_url, safe='')}&branch={quote(branch, safe='')}",
        status_code=303,
    )


@router.get("/lineage-data")
async def get_lineage_data(
    request: Request,
    repo: str,
    branch: str = "main",
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    return await lineage_repo.fetch_lineage_data(user.tenant_id, safe_repo, branch)


@router.get("/lineage-node")
async def get_lineage_node(
    request: Request,
    repo: str,
    branch: str,
    asset_id: str,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    data = await lineage_repo.fetch_node_with_neighbors(
        user.tenant_id, safe_repo, branch, asset_id
    )
    if not data:
        raise HTTPException(status_code=404, detail="Node not found")
    return data


@router.get("/lineage-ui", response_class=HTMLResponse)
async def lineage_ui(
    request: Request,
    repo: str,
    branch: str = "main",
    session: AsyncSession = Depends(get_session),
):
    await _get_user(request, session)
    return templates.TemplateResponse(
        request, "lineage.html", {"repo": repo, "branch": branch}
    )


@router.get("/asset", response_class=HTMLResponse)
async def asset_view(
    request: Request,
    repo: str,
    branch: str,
    asset_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _get_user(request, session)
    return templates.TemplateResponse(
        request, "asset.html", {"repo": repo, "branch": branch, "asset_id": asset_id}
    )


@router.get("/changes", response_class=HTMLResponse)
async def changes_view(
    request: Request,
    repo: str,
    branch: str,
    session: AsyncSession = Depends(get_session),
):
    await _get_user(request, session)
    return templates.TemplateResponse(
        request, "changes.html", {"repo": repo, "branch": branch}
    )
