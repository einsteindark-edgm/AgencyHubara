# ruff: noqa: E402
# (init_otel() va ANTES de importar las activities de eval — mismo Bug A de
#  HU-003: OpenLIT debe parchear litellm antes de que el juez lo use, sino el
#  span gen_ai del JUEZ sale sin tokens/cost. El juez también se mide en SigNoz.)
"""Worker del harness de evaluación del Asesor de Ventas.

Hace dos cosas al boot (igual que `orders/workers/reconcile.py`):
  1. **Asegura un Temporal Schedule** que dispara `SalesEvalWorkflow` en cron
     (default 08:00 / 14:00 / 20:00 hora de Colombia) — la evaluación asíncrona
     "en ciertos momentos del día" sobre tráfico real.
  2. **Corre el worker loop** que ejecuta los workflows + activities de eval.

Idempotente: re-arrancar NO duplica el Schedule (`ScheduleAlreadyRunningError`).
El operador puede disparar una corrida manual desde la Temporal UI/CLI en
cualquier momento (mismo workflow, mismo task queue).

Env:
  * `SALES_EVAL_SCHEDULE_ENABLED` (default "true") — si "false", NO crea el
    Schedule (el worker igual corre → triggers manuales funcionan).
  * `SALES_EVAL_SCHEDULE_CRON` (default "0 8,14,20 * * *", tz America/Bogota).
  * `SALES_EVAL_LOOKBACK_HOURS` (default 8), `SALES_EVAL_MAX_CONVERSATIONS` (50).
  * `EVAL_JUDGE_MODEL` — alias del proxy litellm para el juez (default gemini-backup).

R-DIP: importa `platform/` + el propio plugin `chats`; no plugins siblings.
"""
import asyncio
import os

from src.platform.logging import setup_logging
from src.platform.observability import init_otel, otel_workflow_runner

setup_logging()
init_otel("sales-eval-agent")

from loguru import logger
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)
from temporalio.worker import Worker

from src.platform.plugin_manifest import get_task_queue
from src.platform.temporal.client import get_temporal_client
from src.plugins.chats.agent.sales_eval.activities.eval_activities import (
    evaluate_sales_conversation_activity,
    select_conversations_to_eval_activity,
)
from src.plugins.chats.agent.sales_eval.evals.contracts import EvalWindowInput
from src.plugins.chats.agent.sales_eval.workflows.sales_eval import SalesEvalWorkflow

_SCHEDULE_ID = "sales-eval-schedule"
_WORKFLOW_ID = "sales-eval"
_DEFAULT_CRON = "0 8,14,20 * * *"  # 08:00 / 14:00 / 20:00
_DEFAULT_TZ = "America/Bogota"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        logger.warning("{}={!r} inválido — usando {}", name, raw, default)
        return default
    return val if val > 0 else default


def _window_from_env() -> EvalWindowInput:
    return EvalWindowInput(
        lookback_hours=_env_int("SALES_EVAL_LOOKBACK_HOURS", 8),
        max_conversations=_env_int("SALES_EVAL_MAX_CONVERSATIONS", 50),
    )


async def _ensure_schedule(client: Client, task_queue: str) -> None:
    """Crea el Schedule cron si no existe (idempotente). Off-switch por env."""
    if os.environ.get("SALES_EVAL_SCHEDULE_ENABLED", "true").strip().lower() == "false":
        logger.info("📅 Schedule de evals deshabilitado (SALES_EVAL_SCHEDULE_ENABLED=false)")
        return
    cron = os.environ.get("SALES_EVAL_SCHEDULE_CRON", "").strip() or _DEFAULT_CRON
    try:
        await client.create_schedule(
            _SCHEDULE_ID,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    SalesEvalWorkflow.run,
                    _window_from_env(),
                    id=_WORKFLOW_ID,
                    task_queue=task_queue,
                ),
                spec=ScheduleSpec(
                    cron_expressions=[cron],
                    time_zone_name=_DEFAULT_TZ,
                ),
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
            ),
        )
        logger.info("📅 Schedule '{}' creado — eval cron '{}' ({})", _SCHEDULE_ID, cron, _DEFAULT_TZ)
    except ScheduleAlreadyRunningError:
        logger.info("📅 Schedule '{}' ya existe — no se re-crea", _SCHEDULE_ID)


async def main() -> None:
    logger.info("Conectando sales-eval al clúster Temporal...")
    client = await get_temporal_client()
    task_queue = get_task_queue("chats", "sales_eval")

    await _ensure_schedule(client, task_queue)

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[SalesEvalWorkflow],
        activities=[
            select_conversations_to_eval_activity,
            evaluate_sales_conversation_activity,
        ],
        workflow_runner=otel_workflow_runner(),
    )
    logger.info("🧪 sales-eval worker arriba. Cola: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
