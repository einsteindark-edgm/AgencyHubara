"""Workflow-level tests de OrderSentinelCycleWorkflow (fase roja TDD).

Calco de ReengagementCycleWorkflow: build → (vacío ⇒ skipped_empty sin pagar
cold start) → dispatch → poll loop durable (completed/failed rompen) → si
completed: execute_order_intents con `result["dispatch"]` + los watermarks
TOP-LEVEL del snapshot (`snapshot["watermarks"]` — decisión de contrato de
esta fase roja: el snapshot lleva "hasta dónde analicé" por sesión y el
workflow lo acarrea al execute; run failed ⇒ NO se escriben watermarks, el
próximo ciclo re-analiza).

Activities FAKE registradas POR NOMBRE (molde exacto:
tests/plugins/reengagement/test_cycle_workflow.py). Nombres fijados:
build_order_sentinel_snapshot / dispatch_order_sentinel /
poll_order_sentinel / execute_order_intents.
"""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.order_sentinel.agent.cycle.workflows.cycle import (
    OrderSentinelCycleWorkflow,
)

# Hardcodeado a propósito: el manifest del plugin aún no existe en fase roja
# y get_task_queue() leería el manifest (falso rojo). El worker real usará
# get_task_queue("order_sentinel", "cycle") cuando el manifest lo declare.
QUEUE = "queue-order-sentinel-cycle"

_INTENTS = [
    {
        "kind": "order_stage_intent",
        "session_id": "wa_a",
        "order_id": "order_A",
        "action": "transition",
        "to_stage": "shipping",
        "evidence": ["operador: 'ya salió con el mensajero'"],
        "confidence": 0.9,
    }
]

_RESULT = {
    "schema_version": 1,
    "dispatch": _INTENTS,
    # wa_b cayó en llm_errors (proxy caído para ESA conversación): su watermark
    # NO debe avanzar — el próximo ciclo la re-analiza (PM-006: un intent
    # fallido ≠ una conversación jamás analizada).
    "llm_errors": [{"session_id": "wa_b", "error": "URLError: [Errno 99]"}],
    "suppressed": [],
}

#: La forma REAL del poll de un agente DIRECTO (PM-001): el output compact de
#: Conductor es el STATE COMPLETO del grafo ({payload, classified, result}) —
#: el contrato del agente vive un nivel ADENTRO. El workflow debe descender.
_FULL_GRAPH_STATE = {
    "payload": {"schema_version": 1, "conversations": [{"session_id": "wa_a"}]},
    "classified": [{"session_id": "wa_a", "verdict": None, "error": None}],
    "result": _RESULT,
}

_WATERMARKS = {"wa_a": 1_400, "wa_b": 2_000}


class Tracker:
    def __init__(self) -> None:
        self.dispatched_input: dict | None = None
        self.polls: int = 0
        self.executed: tuple[list, dict] | None = None


def _fakes(
    tracker: Tracker,
    *,
    conversations: list | None = None,
    final_status: str = "completed",
    poll_result: dict | None = None,
):
    convos = (
        conversations
        if conversations is not None
        else [{"session_id": "wa_a", "order_id": "order_A"}]
    )

    @activity.defn(name="build_order_sentinel_snapshot")
    async def fake_snapshot() -> dict:
        return {
            "schema_version": 1,
            "now_ms": 1_752_000_000_000,
            "conversations": convos,
            "watermarks": _WATERMARKS if convos else {},
        }

    @activity.defn(name="dispatch_order_sentinel")
    async def fake_dispatch(snapshot: dict, run_id: str) -> str:
        tracker.dispatched_input = snapshot
        return "eid-123"

    @activity.defn(name="poll_order_sentinel")
    async def fake_poll(execution_id: str) -> dict:
        tracker.polls += 1
        if tracker.polls < 2:  # primer poll: running → el workflow re-pollea
            return {
                "status": "running", "awaiting": None, "result": None, "error": None
            }
        default = _FULL_GRAPH_STATE if final_status == "completed" else None
        result = poll_result if poll_result is not None else default
        error = "node exploded" if final_status == "failed" else None
        return {
            "status": final_status,
            "awaiting": None,
            "result": result,
            "error": error,
        }

    @activity.defn(name="execute_order_intents")
    async def fake_execute(intents: list, watermarks: dict) -> dict:
        tracker.executed = (intents, watermarks)
        return {"applied": 1, "skipped": 0, "failed": 0}

    return [fake_snapshot, fake_dispatch, fake_poll, fake_execute]


async def _run(tracker: Tracker, **kw) -> dict:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[OrderSentinelCycleWorkflow],
            activities=_fakes(tracker, **kw),
        ):
            return await env.client.execute_workflow(
                OrderSentinelCycleWorkflow.run,
                id="order-sentinel-cycle-test",
                task_queue=QUEUE,
            )


@pytest.mark.asyncio
async def test_ciclo_completo_ejecuta_intents_con_los_watermarks_del_snapshot():
    tracker = Tracker()
    summary = await _run(tracker)

    assert tracker.dispatched_input is not None, "debe despachar el snapshot"
    assert tracker.polls >= 2, "re-pollea mientras el run está running"
    assert tracker.executed is not None, "run completed → ejecuta los intents"
    intents, watermarks = tracker.executed
    assert intents == _INTENTS, (
        "PM-001: el poll trae el STATE COMPLETO del grafo — el workflow debe "
        "descender al contrato (state['result']) para extraer el dispatch"
    )
    assert watermarks == {"wa_a": 1_400}, (
        "PM-006: las sesiones en llm_errors (wa_b) NO cierran watermark — "
        "el próximo ciclo las re-analiza; las demás sí avanzan"
    )
    assert summary["applied"] == 1
    assert summary["run_status"] == "completed"


@pytest.mark.asyncio
async def test_result_ya_desenvuelto_tambien_se_extrae():
    """Tolerancia de forma: si el compact/proyección algún día entrega el
    contrato DIRECTO (sin el state alrededor), el descenso no debe romperlo."""
    tracker = Tracker()
    summary = await _run(tracker, poll_result=_RESULT)

    assert tracker.executed is not None
    intents, watermarks = tracker.executed
    assert intents == _INTENTS
    assert watermarks == {"wa_a": 1_400}
    assert summary["run_status"] == "completed"


@pytest.mark.asyncio
async def test_result_sin_dispatch_no_ejecuta_ni_avanza_watermarks():
    """PM-005: si el compact PODÓ el result (o cambió la forma y no hay clave
    `dispatch` en ningún nivel), tratarlo como fallo VISIBLE — sin execute y
    sin watermarks (re-analiza el próximo ciclo), nunca 'completed applied=0'
    que finge que no había trabajo."""
    tracker = Tracker()
    summary = await _run(
        tracker, poll_result={"_pruned_keys": ["result"], "payload": {}}
    )

    assert tracker.executed is None, "sin dispatch extraíble NO se ejecuta nada"
    assert summary["applied"] == 0
    assert summary["run_status"] == "result_missing_dispatch"


@pytest.mark.asyncio
async def test_snapshot_vacio_no_paga_cold_start_ni_ejecuta():
    tracker = Tracker()
    summary = await _run(tracker, conversations=[])

    assert tracker.dispatched_input is None, (
        "sin conversaciones NO se paga el cold start de la caja"
    )
    assert tracker.executed is None
    assert summary["applied"] == 0
    assert summary["run_status"] == "skipped_empty"


@pytest.mark.asyncio
async def test_run_failed_no_ejecuta_intents_ni_escribe_watermarks():
    tracker = Tracker()
    summary = await _run(tracker, final_status="failed")

    assert tracker.executed is None, (
        "run failed → NO se aplican intents (y sin execute no hay watermarks:"
        " el próximo ciclo re-analiza)"
    )
    assert summary["applied"] == 0
    assert summary["run_status"] == "failed"
