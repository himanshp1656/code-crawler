"""
CLI entrypoint to trigger the code-crawler workflow via Temporal directly.

Example:
    python run_connector.py --repo https://github.com/org/repo.git --branch main
"""

import argparse
import asyncio
import os
from typing import Optional

from temporalio.client import Client

from app.workflow import CodeCrawlerWorkflow, TASK_QUEUE

DEFAULT_TENANT_ID = os.getenv("DEFAULT_TENANT_ID", "default")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the code-crawler workflow.")
    parser.add_argument(
        "--repo",
        "--github_repo_url",
        dest="repo",
        required=True,
        help="Git repository URL to crawl.",
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Git branch to use (default: main).",
    )
    parser.add_argument(
        "--language",
        default="python",
        help="Source language (currently only 'python' is supported).",
    )
    parser.add_argument(
        "--tenant",
        default=DEFAULT_TENANT_ID,
        help="Tenant id/slug to isolate data in Postgres (default: %(default)s).",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        default=None,
        help="Optional custom output path for lineage JSON.",
    )
    return parser.parse_args()


async def main(
    github_repo_url: str,
    branch: str,
    language: str,
    output_path: Optional[str],
    tenant_id: str,
) -> None:
    client = await Client.connect("localhost:7233")

    handle = await client.start_workflow(
        CodeCrawlerWorkflow.run,
        args=[github_repo_url, branch, language, output_path, tenant_id],
        id=f"code-crawler-{tenant_id}-{branch}-{github_repo_url.rsplit('/', 1)[-1]}",
        task_queue=TASK_QUEUE,
    )

    result = await handle.result()
    print("Lineage stored in Postgres for repo/branch.")
    print("Assets:", result["stats"].get("assets"))


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(
        main(
            github_repo_url=args.repo,
            branch=args.branch,
            language=args.language,
            output_path=args.output_path,
            tenant_id=args.tenant,
        )
    )

