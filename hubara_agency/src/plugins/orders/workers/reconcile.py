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
from pathlib import Path

import yaml
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
from src.platform.plugin_runtime import ensure_plugin_enabled
from src.platform.temporal.client import get_temporal_client
from src.plugins.orders.agent.activities import reconcile_pending_orders_activity
from src.plugins.orders.agent.activities.emit_stage import emit_order_stage_activity
from src.plugins.orders.agent.contracts import ReconcileInput
from src.plugins.orders.agent.workflows import OrderReconciliationWorkflow
from src.plugins.orders.agent.workflows.emit_stage import EmitOrderStageWorkflow

setup_logging()

_SCHEDULE_ID = "order-reconciliation-schedule"
_WORKFLOW_ID = "order-reconciliation"
# Fallback si el catálogo falta/está roto. El default REAL vive en
# config/schedulers.yaml (orders-reconcile.default) — guardado por
# tests/plugins/orders/test_reconcile_schedule_default.py para que no driftee.
_DEFAULT_INTERVAL_MIN = 5
# Catálogo de schedulers (ACK-3): src/plugins/orders/workers/ → hubara_agency/.
# Cada plugin lo lee de forma independiente (stdlib+yaml) — NO un módulo central
# compartido (rompería R-DIP / ratchet P-28). Leer config en worker_boot/main es
# R-DET-safe (no es un workflow).
_CATALOG_PATH = Path(__file__).resolve().parents[4] / "config" / "schedulers.yaml"


def _catalog_default(scheduler_id: str, fallback: str) -> str:
    """Lee `default` de un scheduler del catálogo; cae al fallback si no se puede."""
    try:
        data = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
        for entry in data["schedulers"]:
            if entry.get("id") == scheduler_id and entry.get("default") is not None:
                return str(entry["default"])
    except Exception:  # noqa: BLE001 — el boot del worker sobrevive un catálogo ausente/roto
        logger.warning(
            "No pude leer el default de '{}' del catálogo ({}) — uso fallback {!r}",
            scheduler_id, _CATALOG_PATH, fallback,
        )
    return fallback


def _interval_minutes() -> int:
    """Intervalo del Schedule en minutos (env ORDER_RECONCILE_INTERVAL_MINUTES).

    Precedencia: env > catálogo (config/schedulers.yaml) > fallback constante.
    """
    raw = os.environ.get("ORDER_RECONCILE_INTERVAL_MINUTES", "").strip()
    if not raw:
        raw = _catalog_default("orders-reconcile", str(_DEFAULT_INTERVAL_MIN))
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
    ensure_plugin_enabled("orders")  # P-21: self-gate del toggle (INV-2)
    logger.info("Conectando orders/reconcile al cluster Temporal...")
    client = await get_temporal_client()
    task_queue = get_task_queue("orders", "reconcile")

    await _ensure_schedule(client, task_queue)

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[OrderReconciliationWorkflow, EmitOrderStageWorkflow],
        activities=[reconcile_pending_orders_activity, emit_order_stage_activity],
    )
    logger.info("🧾 orders/reconcile worker arriba. Cola: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
