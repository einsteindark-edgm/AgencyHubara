"""reengagement cycle worker — registra el ReengagementCycleWorkflow +
activities en queue-reengagement-cycle. Solo imports src.sdk (P-28)."""
from __future__ import annotations

import asyncio

from loguru import logger
from temporalio.worker import Worker

from src.plugins.reengagement.agent.cycle.activities import (
    build_reengagement_snapshot_activity,
    dispatch_window_strategist_activity,
    poll_window_strategist_activity,
)
from src.plugins.reengagement.agent.cycle.workflows.cycle import (
    ReengagementCycleWorkflow,
)
from src.sdk import ensure_plugin_enabled, get_task_queue
from src.sdk.eventkit import dispatch_event_activity
from src.sdk.runtime import get_temporal_client, setup_logging

setup_logging()


async def main() -> None:
    ensure_plugin_enabled("reengagement")  # P-21: self-gate del toggle (INV-2)
    logger.info("Conectando reengagement cycle al cluster Temporal...")
    client = await get_temporal_client()

    task_queue = get_task_queue("reengagement", "cycle")
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[ReengagementCycleWorkflow],
        activities=[
            build_reengagement_snapshot_activity,
            dispatch_window_strategist_activity,
            poll_window_strategist_activity,
            dispatch_event_activity,
        ],
    )

    logger.info("🔁 reengagement cycle worker arriba. Cola: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
