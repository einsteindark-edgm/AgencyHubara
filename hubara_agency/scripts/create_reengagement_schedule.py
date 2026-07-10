"""Crea (idempotente) el Temporal Schedule del ciclo Window Strategist.

Deploy-time helper (P-20-adjacent): el ReengagementCycleWorkflow es one-shot;
la cadencia la da este Schedule. Default 45 min — cada ciclo paga el cold
start EC2 de la caja GraphAgents (1-3 min), así que NO bajar a minutos.

Uso:
    cd hubara_agency && uv run python scripts/create_reengagement_schedule.py
    REENGAGEMENT_CYCLE_MINUTES=60 uv run python scripts/create_reengagement_schedule.py
"""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta

from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleSpec,
)

from src.sdk import ensure_plugin_enabled, get_task_queue
from src.sdk.runtime import get_temporal_client

SCHEDULE_ID = "reengagement-cycle-schedule"


async def main() -> None:
    ensure_plugin_enabled("reengagement")
    minutes = int(os.environ.get("REENGAGEMENT_CYCLE_MINUTES", "45"))
    client = await get_temporal_client()
    try:
        await client.create_schedule(
            SCHEDULE_ID,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    "ReengagementCycleWorkflow",
                    id="reengagement-cycle",
                    task_queue=get_task_queue("reengagement", "cycle"),
                ),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(every=timedelta(minutes=minutes))]
                ),
            ),
        )
        print(f"schedule {SCHEDULE_ID} creado: cada {minutes} min")
    except ScheduleAlreadyRunningError:
        print(f"schedule {SCHEDULE_ID} ya existe — no-op (borrálo para recrear)")


if __name__ == "__main__":
    asyncio.run(main())
