"""Procedencia de las fixtures CONGELADAS del gate `continue-as-new-per-run-v1`.

NO es parte del flujo normal: las fixtures ya están commiteadas y NO se
regeneran. Este script queda como registro reproducible de cómo se produjeron.

El bug (auditoría de Temporal, 2026-10-15): el run que nace de un
continue-as-new trae el `turn_count` acumulado (50) y el tope lo comparaba con
ese contador → desde el turno 50, cada turno hacía continue-as-new y repetía el
bootstrap. Estas histories son ESE run, con la regla vieja: arranca con 50
turnos acumulados, el cliente escribe, un turno y continue-as-new. Un run vivo
nunca trae esta forma (ya cortó); la historia de uno que cortó sí, y con la
regla nueva no se reproduce sin el gate.

Generadas con el código de `main` en 16c03158 (antes de este cambio), una por
versión del workflow de ventas (`[v1]` y `[v2]`) y una del ETA (el mismo tope
por avisos de estado):

    git archive 16c03158 hubara_agency | tar -x -C /tmp/old
    cp hubara_agency/tests/fixtures/generate_continue_as_new_prepatch_fixtures.py \\
        /tmp/old/hubara_agency/tests/fixtures/
    cd /tmp/old/hubara_agency && PYTHONPATH=. <repo>/.venv/bin/python \\
        tests/fixtures/generate_continue_as_new_prepatch_fixtures.py /tmp/out_dir

Corrido contra el código nuevo no corta tras el turno y la fixture no
protegería nada: el script se niega.

Escenario (sesión sintética `wa_can_loop`): run con `turn_count=50`, «Hola,
sigo acá» → un turno → continue-as-new. ETA: run con `turn_count=50`, un aviso
de `shipping` → continue-as-new.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

FIXTURES = {
    "v1": "history_sales_continue_as_new_loop_prepatch_v1.json",
    "v2": "history_sales_v2_continue_as_new_loop_prepatch_v1.json",
}
SESSION = "wa_can_loop"
ETA_FIXTURE = "history_eta_continue_as_new_loop_prepatch_v1.json"


async def _generate(version: str) -> str:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from src.plugins.chats.agent.sales.contracts import SalesSessionInput
    from src.plugins.chats.agent.sales.workflows.sales_session import _CONTINUE_AS_NEW_AFTER_TURNS
    from tests.sales_workflow_versions import VERSIONS
    from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _make_fake_activities

    wf = VERSIONS[version]
    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[wf],
            activities=_make_fake_activities(tracker, workspace_path="/fixture/workspace"),
        ):
            handle = await env.client.start_workflow(
                wf.run,
                SalesSessionInput(
                    session_id=SESSION,
                    turn_count=_CONTINUE_AS_NEW_AFTER_TURNS,
                    runtime_workspace_path="/fixture/workspace",
                ),
                id=f"session-{SESSION}",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(wf.send_message, args=["Hola, sigo acá", None, None])
            await handle.result()
            # La history del PRIMER run (el que cortó), no la del último.
            first = env.client.get_workflow_handle(handle.id, run_id=handle.first_execution_run_id)
            history = await first.fetch_history()

    return _sanitized_cut(history.to_json())


async def _generate_eta() -> str:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from src.plugins.eta.agent.eta.workflows.eta_session import _CONTINUE_AS_NEW_AFTER_TURNS, HubaraEtaSessionWorkflow
    from tests.plugins.eta.test_eta_ready_photo_workflow import QUEUE, Tracker, _fakes

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=QUEUE, workflows=[HubaraEtaSessionWorkflow], activities=_fakes(Tracker(has_photo=False))
        ):
            handle = await env.client.start_workflow(
                "HubaraEtaSessionWorkflow",
                {"session_id": SESSION, "order_id": "order_A", "turn_count": _CONTINUE_AS_NEW_AFTER_TURNS},
                id=f"eta-{SESSION}",
                task_queue=QUEUE,
            )
            stage = {"session_id": SESSION, "order_id": "order_A", "to_stage": "shipping"}
            await handle.signal("notify_stage_change", stage)
            await handle.result()
            first = env.client.get_workflow_handle(handle.id, run_id=handle.first_execution_run_id)
            history = await first.fetch_history()
    return _sanitized_cut(history.to_json())


def _sanitized_cut(raw: str) -> str:
    last = json.loads(raw)["events"][-1]
    assert "workflowExecutionContinuedAsNewEventAttributes" in last, "el run no cortó tras el turno"
    # Saneado: la identidad del worker (pid@ip local) → `fixture-worker`.
    return re.sub(r'("identity":\s*")[^"]+(")', r"\1fixture-worker\2", raw)


async def _main(out_dir: Path) -> None:
    import src.sdk.agentkit as kit

    if hasattr(kit, "continue_as_new_due"):
        raise SystemExit("este código ya trae el gate: generá con el código de 16c03158")
    for version, name in FIXTURES.items():
        (out_dir / name).write_text(await _generate(version), encoding="utf-8")
        print(f"escrita {out_dir / name}")
    (out_dir / ETA_FIXTURE).write_text(await _generate_eta(), encoding="utf-8")
    print(f"escrita {out_dir / ETA_FIXTURE}")


if __name__ == "__main__":
    asyncio.run(_main(Path(sys.argv[1])))
