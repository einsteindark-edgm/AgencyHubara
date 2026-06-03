"""orders/reconcile worker — registra el OrderReconciliationWorkflow + activity
y ASEGURA el Temporal Schedule periódico al boot.

Esto es lo que hace que el reintento "se active solo": al levantar el container
`hubara-worker-orders-reconcile`, el worker (a) crea el Schedule si no existe
y (b) corre el loop que ejecuta los workflows que el Schedule dispara. El
Schedule corre `OrderReconciliationWorkflow` cada `ORDER_RECONCILE_INTERVAL_MINUTES`
(default 5), con `overlap=SKIP` (nunca dos barridos solapados).

Idempotente en dos planos:
  * Re-arrancar el worker NO duplica el Schedule (`ScheduleAlreadyRunningError`
    se absorbe).
  * Cada barrido es idempotente (`reconcile_one` no duplica drafts en Medusa).

R-DIP: importa de `platform/` y del propio plugin `orders`; NO de plugins
siblings. R-STATELESS: el `_SCHEDULE_ID` es una constante, no estado mutable.
"""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta

from loguru import logger
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)
from temporalio.worker import Worker

from src.platform.logging import setup_logging
from src.platform.plugin_manifest import get_task_queue
from src.platform.temporal.client import get_temporal_client
from src.plugins.orders.agent.activities import reconcile_pending_orders_activity
from src.plugins.orders.agent.contracts import ReconcileInput
from src.plugins.orders.agent.workflows import OrderReconciliationWorkflow

setup_logging()

_SCHEDULE_ID = "order-reconciliation-schedule"
_WORKFLOW_ID = "order-reconciliation"
_DEFAULT_INTERVAL_MIN = 5


def _interval_minutes() -> int:
    """Intervalo del Schedule en minutos (env ORDER_RECONCILE_INTERVAL_MINUTES)."""
    raw = os.environ.get("ORDER_RECONCILE_INTERVAL_MINUTES", "").strip()
    if not raw:
        return _DEFAULT_INTERVAL_MIN
    try:
        val = int(raw)
    except ValueError:
        logger.warning(
            "ORDER_RECONCILE_INTERVAL_MINUTES={!r} inválido — usando {}",
            raw, _DEFAULT_INTERVAL_MIN,
        )
        return _DEFAULT_INTERVAL_MIN
    return val if val > 0 else _DEFAULT_INTERVAL_MIN


async def _ensure_schedule(client: Client, task_queue: str) -> None:
    """Crea el Schedule periódico si no existe (idempotente al re-arrancar)."""
    minutes = _interval_minutes()
    try:
        await client.create_schedule(
            _SCHEDULE_ID,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    OrderReconciliationWorkflow.run,
                    ReconcileInput(),
                    id=_WORKFLOW_ID,
                    task_queue=task_queue,
                ),
                spec=ScheduleSpec(
                    intervals=[
                        ScheduleIntervalSpec(every=timedelta(minutes=minutes))
                    ],
                ),
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
            ),
        )
        logger.info(
            "📅 Schedule '{}' creado — reconciliación cada {} min",
            _SCHEDULE_ID, minutes,
        )
    except ScheduleAlreadyRunningError:
        logger.info("📅 Schedule '{}' ya existe — no se re-crea", _SCHEDULE_ID)


async def main() -> None:
    logger.info("Conectando orders/reconcile al cluster Temporal...")
    client = await get_temporal_client()
    task_queue = get_task_queue("orders", "reconcile")

    await _ensure_schedule(client, task_queue)

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[OrderReconciliationWorkflow],
        activities=[reconcile_pending_orders_activity],
    )
    logger.info("🧾 orders/reconcile worker arriba. Cola: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
