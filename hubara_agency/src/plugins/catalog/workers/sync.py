"""catalog_sync worker — registra workflow + activities en CATALOG_SYNC_QUEUE."""
from __future__ import annotations

import asyncio

from loguru import logger
from temporalio.worker import Worker

from src.plugins.catalog.agent.activities import (
    pull_medusa_catalog_activity,
    write_snapshot_activity,
)
from src.plugins.catalog.agent.workflows import CatalogSyncWorkflow
from src.platform.plugin_manifest import get_task_queue
from src.platform.logging import setup_logging
from src.platform.temporal.client import get_temporal_client

setup_logging()


async def main() -> None:
    logger.info("Conectando catalog_sync al cluster Temporal...")
    client = await get_temporal_client()

    task_queue = get_task_queue("catalog", "sync")
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[CatalogSyncWorkflow],
        activities=[
            pull_medusa_catalog_activity,
            write_snapshot_activity,
        ],
    )

    logger.info(
        "📦 catalog_sync worker arriba. Cola: '{}'", task_queue
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
