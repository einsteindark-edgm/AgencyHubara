"""El cierre por abandono del bot V2 no muere si Jev tarda (premortem 2026-10-09).

`decide_ghosting_action(session)` espera la capacidad `cierre` (Jev, hasta
10,25 s) y tenía 10 s con 2 intentos y sin red: con Jev lento, los dos
intentos vencían, el workflow fallaba y nadie etiquetaba ni perseguía los
datos de envío del cliente. Ahora tiene 30 s y, si aun así falla, sale el
aviso de la regla (`decide_ghosting_action("")`, sin I/O).
"""
from __future__ import annotations

from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

QUEUE = "test-sales-ghosting-v2"
CALLS: list[str] = []


@activity.defn(name="decide_ghosting_action")
async def _decide_ghosting_action(session_id: str = "") -> str:
    CALLS.append(session_id)
    if session_id:
        raise RuntimeError("Jev no respondió")
    return "[SISTEMA] aviso de la regla"


@workflow.defn(name="GhostingProbeWorkflow")
class _GhostingProbeWorkflow:
    @workflow.run
    async def run(self, session_id: str) -> str:
        from src.plugins.chats.agent.sales.workflows.ghosting_v2 import ghost_trigger

        return await ghost_trigger(session_id)


async def test_a_failed_decision_falls_back_to_the_rule_notice() -> None:
    CALLS.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=QUEUE, workflows=[_GhostingProbeWorkflow],
            activities=[_decide_ghosting_action], workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            out = await env.client.execute_workflow(
                _GhostingProbeWorkflow.run, "wa_573001234567", id="ghosting-probe", task_queue=QUEUE
            )

    assert out == "[SISTEMA] aviso de la regla"
    assert CALLS[-1] == "" and "wa_573001234567" in CALLS
