from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.crawl_job import CrawlJob
from app.repositories.user_repo import UserRepository

router = APIRouter()

_STATUS_MAP = {
    1: "running",
    2: "completed",
    3: "failed",
    4: "cancelled",
    5: "terminated",
    6: "running",   # continued_as_new
    7: "timed_out",
}


async def _get_user(request: Request, session: AsyncSession):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="User inactive or not found")
    return user


async def insert_crawl_job(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: int,
    workflow_id: str,
    repo: str,
    branch: str,
    triggered_by: str,
) -> None:
    job = CrawlJob(
        tenant_id=tenant_id,
        user_id=user_id,
        workflow_id=workflow_id,
        repo=repo,
        branch=branch,
        triggered_by=triggered_by,
    )
    session.add(job)
    await session.flush()


@router.get("")
async def list_workflows(request: Request, session: AsyncSession = Depends(get_session)):
    user = await _get_user(request, session)
    client = request.app.state.temporal_client

    result = await session.execute(
        select(CrawlJob)
        .where(CrawlJob.tenant_id == user.tenant_id)
        .order_by(CrawlJob.started_at.desc())
        .limit(30)
    )
    jobs = result.scalars().all()

    out = []
    for job in jobs:
        status = "unknown"
        error = None
        try:
            handle = client.get_workflow_handle(job.workflow_id)
            desc = await handle.describe()
            status = _STATUS_MAP.get(desc.status.value, "unknown")
            if status == "failed":
                try:
                    await handle.result()
                except Exception as e:
                    error = str(e)
        except Exception:
            status = "unknown"

        out.append({
            "id": job.id,
            "workflow_id": job.workflow_id,
            "repo": job.repo,
            "branch": job.branch,
            "triggered_by": job.triggered_by,
            "started_at": job.started_at.isoformat(),
            "status": status,
            "error": error,
        })

    return {"jobs": out}


@router.delete("/{job_id}")
async def delete_workflow(job_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    user = await _get_user(request, session)
    result = await session.execute(
        select(CrawlJob).where(CrawlJob.id == job_id, CrawlJob.tenant_id == user.tenant_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await session.delete(job)
    await session.commit()
    return {"ok": True}
