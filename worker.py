"""
Temporal worker entrypoint for the code-crawler workflow.

Run this after starting a local Temporal server:
    temporal server start-dev

Then in another terminal (from this project root):
    python worker.py
"""

import asyncio
import logging

from temporalio import worker
from temporalio.client import Client

from app import activities
from app.db import configure as configure_db
from app.workflow import TASK_QUEUE, CodeCrawlerWorkflow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    configure_db()

    client = await Client.connect("localhost:7233")

    w = worker.Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[CodeCrawlerWorkflow],
        activities=[
            activities.clone_repo_activity,
            activities.parse_repo_activity,
            activities.build_lineage_activity,
        ],
    )

    logger.info("Starting Temporal worker on task queue %s", TASK_QUEUE)
    await w.run()


if __name__ == "__main__":
    asyncio.run(main())
