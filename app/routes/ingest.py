from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories.user_repo import UserRepository
from app.schemas.auth import IngestionRequest, IngestionResponse
from app.workflow import TASK_QUEUE, CodeCrawlerWorkflow

router = APIRouter()


@router.post("/ingest", response_model=IngestionResponse)
async def start_ingestion(
    request: Request,
    req: IngestionRequest,
    session: AsyncSession = Depends(get_session),
):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="User inactive or not found")

    if req.tenant_id != user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")

    client = request.app.state.temporal_client
    assert client is not None, "Temporal client not initialised"

    handle = await client.start_workflow(
        CodeCrawlerWorkflow.run,
        args=[
            req.github_repo_url,
            req.branch,
            req.language,
            req.output_path,
            user.tenant_id,
        ],
        id=f"code-crawler-{user.tenant_id}-{req.branch}-{req.github_repo_url.rsplit('/', 1)[-1]}",
        task_queue=TASK_QUEUE,
    )

    return IngestionResponse(workflow_id=handle.id, run_id=handle.result_run_id)
