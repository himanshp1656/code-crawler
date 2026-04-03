from datetime import timedelta
import logging
from typing import Any, Dict, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy


TASK_QUEUE = "code-crawler-task-queue"
logger = logging.getLogger(__name__)



@workflow.defn
class CodeCrawlerWorkflow:
    """
    Temporal workflow for crawling a Python GitHub repository and building lineage.

    Inputs:
        github_repo_url: URL of the Git repository
        branch: branch name (default: "main")
        language: "python" (only language supported for now)
        output_path: optional custom JSON output path
    """

    @workflow.run
    async def run(
        self,
        github_repo_url: str,
        branch: str = "main",
        language: str = "python",
        output_path: Optional[str] = None,
        tenant_id: str = "default",
    ) -> Dict[str, Any]:
        logger.info(
            "workflow: starting CodeCrawlerWorkflow for repo=%s branch=%s language=%s",
            github_repo_url,
            branch,
            language,
        )
    
        workflow_args: Dict[str, Any] = {
            "github_repo_url": github_repo_url,
            "branch": branch,
            "language": language,
            "tenant_id": tenant_id,
            "workflow_id": workflow.info().workflow_id,
            "run_id": workflow.info().run_id,
        }
        if output_path:
            workflow_args["output_path"] = output_path

        retry_policy = RetryPolicy(
            initial_interval=timedelta(seconds=2),
            backoff_coefficient=2.0,
            maximum_attempts=5,
        )

        # 1. Clone repository
        logger.info("workflow: step 1 - clone_repo_activity")
        workflow_args = await workflow.execute_activity(
            "clone_repo_activity",
            args=[workflow_args],
            task_queue=TASK_QUEUE,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        # 2. Parse repository
        logger.info("workflow: step 2 - parse_repo_activity")
        workflow_args = await workflow.execute_activity(
            "parse_repo_activity",
            args=[workflow_args],
            task_queue=TASK_QUEUE,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
        )

        # 3. Build lineage
        logger.info("workflow: step 3 - build_lineage_activity")
        workflow_args = await workflow.execute_activity(
            "build_lineage_activity",
            args=[workflow_args],
            task_queue=TASK_QUEUE,
            start_to_close_timeout=timedelta(minutes=50),
            retry_policy=retry_policy,
        )

        logger.info(
            "workflow: completed, stats=%s",
            workflow_args.get("lineage_stats"),
        )

        return {
            "stats": workflow_args.get("lineage_stats", {}),
        }

