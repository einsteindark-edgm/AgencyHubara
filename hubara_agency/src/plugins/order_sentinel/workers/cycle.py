"""order_sentinel cycle worker — registra el OrderSentinelCycleWorkflow +
activities en queue-order-sentinel-cycle. Solo imports src.sdk (P-28)."""
from __future__ import annotations

import asyncio

from loguru import logger
from temporalio.worker import Worker

from src.plugins.order_sentinel.agent.cycle.activities import (
    build_order_sentinel_snapshot_activity,
    dispatch_order_sentinel_activity,
    execute_order_intents_activity,
    poll_order_sentinel_activity,
)
from src.plugins.order_sentinel.agent.cycle.workflows.cycle import (
    OrderSentinelCycleWorkflow,
)
from src.sdk import ensure_plugin_enabled, get_task_queue
from src.sdk.runtime import get_temporal_client, setup_logging

setup_logging()


async def main() -> None:
    ensure_plugin_enabled("order_sentinel")  # P-21: self-gate del toggle (INV-2)
    logger.info("Conectando order_sentinel cycle al cluster Temporal...")
    client = await get_temporal_client()

    task_queue = get_task_queue("order_sentinel", "cycle")
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[OrderSentinelCycleWorkflow],
        activities=[
            build_order_sentinel_snapshot_activity,
            dispatch_order_sentinel_activity,
            poll_order_sentinel_activity,
            execute_order_intents_activity,
        ],
    )

    logger.info("🛰️ order_sentinel cycle worker arriba. Cola: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
