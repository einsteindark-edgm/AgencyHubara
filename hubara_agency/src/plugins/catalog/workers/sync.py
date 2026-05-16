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
from src.platform.constants import CATALOG_SYNC_QUEUE
from src.platform.logging import setup_logging
from src.platform.temporal.client import get_temporal_client

setup_logging()


async def main() -> None:
    logger.info("Conectando catalog_sync al cluster Temporal...")
    client = await get_temporal_client()

    worker = Worker(
        client,
        task_queue=CATALOG_SYNC_QUEUE,
        workflows=[CatalogSyncWorkflow],
        activities=[
            pull_medusa_catalog_activity,
            write_snapshot_activity,
        ],
    )

    logger.info(
        "📦 catalog_sync worker arriba. Cola: '{}'", CATALOG_SYNC_QUEUE
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
