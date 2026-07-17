"""Worker del scheduler post-venta (plugin chats).

Al boot (patrón `sales_eval` / `orders/reconcile`):
  1. **Asegura un Temporal Schedule** que dispara `PostSaleReturnWorkflow`
     1x/día — devuelve al bot de ventas las conversaciones con compra cerrada
     (`COMPRA_EXITOSA`), pago confirmado y **pedido ENTREGADO** que quedaron
     abiertas en humano sin robot corriendo (mientras la orden está en
     proceso, el humano sigue gestionándola — no se toca).
  2. **Corre el worker loop** (workflow + activities del ciclo).

Idempotente: re-arrancar NO duplica el Schedule y CONVERGE el cron a config.

Env:
  * `POST_SALE_RETURN_SCHEDULE_ENABLED` (default "true") — "false" BORRA el
    schedule existente (toggle real, INV-2); el worker igual corre → triggers
    manuales desde la Temporal UI funcionan.
  * `POST_SALE_RETURN_SCHEDULE_CRON` (default "0 21 * * *", tz America/Bogota
    — después del cierre del día del operador).

Solo imports `src.sdk` + el propio plugin (P-28).
"""
from __future__ import annotations

import asyncio
import os

from loguru import logger
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.worker import Worker

from src.plugins.chats.agent.post_sale_return.activities import (
    return_post_sale_session_to_sales_activity,
    scan_post_sale_human_sessions_activity,
)
from src.plugins.chats.agent.post_sale_return.workflows import (
    PostSaleReturnWorkflow,
)
from src.sdk import ensure_plugin_enabled, get_task_queue
from src.sdk.runtime import get_temporal_client, setup_logging

setup_logging()

_SCHEDULE_ID = "post-sale-return-schedule"
_WORKFLOW_ID = "post-sale-return"
_DEFAULT_CRON = "0 21 * * *"
_DEFAULT_TZ = "America/Bogota"


async def _ensure_schedule(client: Client, task_queue: str) -> None:
    """Sincroniza el Schedule diario con el toggle y el cron de config.

    Idempotente en AMBOS sentidos (toggle real, INV-2): apagar el toggle no
    solo skipea la creación — BORRA el schedule existente, así el env gobierna
    el estado real en Temporal. Con el schedule ya creado, CONVERGE el cron al
    valor de config (create_schedule no actualiza uno existente server-side).
    """
    enabled = os.environ.get("POST_SALE_RETURN_SCHEDULE_ENABLED", "true")
    if enabled.strip().lower() == "false":
        try:
            await client.get_schedule_handle(_SCHEDULE_ID).delete()
            logger.info(
                "📅 Schedule '{}' BORRADO (POST_SALE_RETURN_SCHEDULE_ENABLED=false)",
                _SCHEDULE_ID,
            )
        except Exception as exc:  # noqa: BLE001 — normalmente "no existía"
            # Visible por si el delete falló por RPC real (no por ausencia):
            # el toggle-off quedaría sin efecto hasta el próximo boot (INV-2).
            logger.info(
                "📅 Schedule post-venta deshabilitado (delete no-op: {}: {})",
                type(exc).__name__,
                exc,
            )
        return
    cron = os.environ.get("POST_SALE_RETURN_SCHEDULE_CRON", "").strip() or _DEFAULT_CRON
    desired_spec = ScheduleSpec(cron_expressions=[cron], time_zone_name=_DEFAULT_TZ)
    try:
        await client.create_schedule(
            _SCHEDULE_ID,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    PostSaleReturnWorkflow.run,
                    id=_WORKFLOW_ID,
                    task_queue=task_queue,
                ),
                spec=desired_spec,
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
            ),
        )
        logger.info(
            "📅 Schedule '{}' creado — cron '{}' ({})", _SCHEDULE_ID, cron, _DEFAULT_TZ
        )
    except ScheduleAlreadyRunningError:
        # Converge el cron al valor de config (preserva state/pausa).
        def _converge(inp: ScheduleUpdateInput) -> ScheduleUpdate:
            inp.description.schedule.spec = desired_spec
            return ScheduleUpdate(schedule=inp.description.schedule)

        await client.get_schedule_handle(_SCHEDULE_ID).update(_converge)
        logger.info(
            "📅 Schedule '{}' actualizado — cron '{}' ({})",
            _SCHEDULE_ID,
            cron,
            _DEFAULT_TZ,
        )


async def main() -> None:
    ensure_plugin_enabled("chats")  # P-21: self-gate del toggle (INV-2)
    logger.info("Conectando post-sale-return al clúster Temporal...")
    client = await get_temporal_client()
    task_queue = get_task_queue("chats", "post_sale_return")

    await _ensure_schedule(client, task_queue)

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[PostSaleReturnWorkflow],
        activities=[
            scan_post_sale_human_sessions_activity,
            return_post_sale_session_to_sales_activity,
        ],
    )
    logger.info("🔁 post-sale-return worker arriba. Cola: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
