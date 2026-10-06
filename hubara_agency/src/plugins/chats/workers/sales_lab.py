"""Worker del laboratorio de conversaciones — corre SOLO en la caja del laboratorio.

LABORATORIO_CONVERSACIONES_PLAN.md §3.3–§3.6. Lo arranca `dispatch.sh` (una
vez por corrida, `LAB_RUN_ID`) desde el compose de la caja
(`infra/compose/lab/docker-compose.lab.yml`). NO está en el manifiesto: nunca
se renderiza en el compose de producción ni en el local.

Orden de arranque (candado 3 del plan §3.5):
  1. fija las carpetas dentro de /lab y apaga la telemetría ANTES de importar
     la app (la composición guarda rutas y clientes con lru_cache);
  2. el guard: sin llaves de producción, Temporal de la caja, carpetas en /lab;
  3. el self-gate del plugin;
  4. corre `LabRunWorkflow` en el Temporal local y termina (la caja se apaga
     sola cuando no queda trabajo).
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from src.plugins.chats.agent.sales_lab.guard import check_lab_env, prepare_lab_env


def run_input_from_env(env) -> tuple[Any, str]:
    """La orden de `dispatch.sh`: `LAB_RUN_ID` y, para solo calificar lo ya
    simulado con las respuestas de Claude Code, `LAB_RUN_MODE=evaluate`."""
    from src.plugins.chats.agent.sales_lab.run.contracts import LabRunInput

    run_id = str(env.get("LAB_RUN_ID") or "")
    if (env.get("LAB_RUN_MODE") or "").strip() == "evaluate":
        return LabRunInput(run_id=run_id, mode="evaluate"), f"lab-eval-{run_id}"
    return LabRunInput(run_id=run_id), f"lab-run-{run_id}"


async def _run(env) -> dict:
    from temporalio.worker import Worker

    from src.plugins.chats.agent.sales_lab.run.activities import LAB_RUN_ACTIVITIES
    from src.plugins.chats.agent.sales_lab.run.contracts import LAB_TASK_QUEUE
    from src.plugins.chats.agent.sales_lab.run.workflow import LabRunWorkflow
    from src.sdk.runtime import get_temporal_client

    run_input, workflow_id = run_input_from_env(env)
    client = await get_temporal_client()
    async with Worker(client, task_queue=LAB_TASK_QUEUE, workflows=[LabRunWorkflow], activities=LAB_RUN_ACTIVITIES):
        return await client.execute_workflow(
            LabRunWorkflow.run, run_input, id=workflow_id, task_queue=LAB_TASK_QUEUE
        )


def main() -> None:
    prepare_lab_env(os.environ)
    problems = check_lab_env(os.environ)
    if problems:
        print("sales_lab NO arranca (guard del laboratorio):", *problems, sep="\n  ", file=sys.stderr)
        sys.exit(2)
    from src.sdk import ensure_plugin_enabled

    ensure_plugin_enabled("chats")  # P-21: self-gate del toggle (INV-2)
    run_id = os.environ.get("LAB_RUN_ID", "")
    if not run_id:
        print("sales_lab: falta LAB_RUN_ID", file=sys.stderr)
        sys.exit(2)
    result = asyncio.run(_run(os.environ))
    print(f"sales_lab: corrida {run_id} terminó en {result.get('phase')}")


if __name__ == "__main__":
    main()
