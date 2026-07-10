"""Crea (idempotente) el Temporal Schedule del ciclo Order Sentinel.

Deploy-time helper (P-20-adjacent): el OrderSentinelCycleWorkflow es one-shot;
la cadencia la da este Schedule. Default: 1 vez al DÍA a la 01:00 UTC
(20:00 Bogotá — fin de la jornada del operador) — cada ciclo paga el cold
start EC2 de la caja GraphAgents y el watermark por sesión hace que un ciclo
diario procese todo lo acumulado. NO bajar a minutos.

Uso:
    cd hubara_agency && uv run python scripts/create_order_sentinel_schedule.py
    ORDER_SENTINEL_CYCLE_HOUR_UTC=13 uv run python scripts/create_order_sentinel_schedule.py
"""
from __future__ import annotations

import asyncio
import os

from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    ScheduleRange,
    ScheduleSpec,
)

from src.sdk import ensure_plugin_enabled, get_task_queue
from src.sdk.runtime import get_temporal_client

SCHEDULE_ID = "order-sentinel-cycle-schedule"


async def main() -> None:
    ensure_plugin_enabled("order_sentinel")
    hour_utc = int(os.environ.get("ORDER_SENTINEL_CYCLE_HOUR_UTC", "1"))
    client = await get_temporal_client()
    try:
        await client.create_schedule(
            SCHEDULE_ID,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    "OrderSentinelCycleWorkflow",
                    id="order-sentinel-cycle",
                    task_queue=get_task_queue("order_sentinel", "cycle"),
                ),
                spec=ScheduleSpec(
                    calendars=[
                        ScheduleCalendarSpec(hour=[ScheduleRange(hour_utc)])
                    ]
                ),
            ),
        )
        print(f"schedule {SCHEDULE_ID} creado: diario a las {hour_utc:02d}:00 UTC")
    except ScheduleAlreadyRunningError:
        print(f"schedule {SCHEDULE_ID} ya existe — no-op (borrálo para recrear)")


if __name__ == "__main__":
    asyncio.run(main())
