"""Workflow-level test del ReengagementCycleWorkflow (WS-B4).

Un ciclo: snapshot → dispatch a la caja GraphAgents → poll hasta terminal →
un ReengagementDispatchIntentEvent POR intent del run.result. El workflow no
manda mensajes: el remarketing (via transition + gate central) es quien toca
al cliente.
"""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.reengagement.agent.cycle.workflows.cycle import (
    ReengagementCycleWorkflow,
)
from src.sdk import get_task_queue
from src.sdk.eventkit import EventEnvelope

QUEUE = get_task_queue("reengagement", "cycle")

_RESULT = {
    "schema_version": 1,
    "snapshot_now_ms": 1716700000000,
    "dispatch": [
        {
            "kind": "reengagement_dispatch",
            "session_id": "wa_a",
            "recommended_channel": "template",
            "recommended_category": "utility",
            "reason": "transactional_hook",
            "priority": 1,
        },
        {
            "kind": "reengagement_dispatch",
            "session_id": "wa_b",
            "recommended_channel": "free_form",
            "recommended_category": "service",
            "reason": "csw_free_form",
            "priority": 2,
        },
    ],
    "suppressed": [{"session_id": "wa_c", "reason": "fase_b_cold_suppressed"}],
    "truncated_by_budget": 0,
}

#: La forma REAL del poll de un agente DIRECTO (PM-001, premortem
#: order-sentinel): el output compact de Conductor es el STATE COMPLETO del
#: grafo del window-strategist ({payload, classified, result}) — el contrato
#: vive un nivel ADENTRO. Con la extracción vieja (`state["result"]["dispatch"]`)
#: esto daba dispatched=0 con run completed: feature muerta en silencio.
_FULL_GRAPH_STATE = {
    "payload": {"schema_version": 1, "conversations": [{"session_id": "wa_a"}]},
    "classified": [{"session_id": "wa_a", "action": "reactivate"}],
    "result": _RESULT,
}


class Tracker:
    def __init__(self) -> None:
        self.dispatched_input: dict | None = None
        self.polls: int = 0
        self.envelopes: list[EventEnvelope] = []


def _fakes(
    tracker: Tracker,
    *,
    conversations: list | None = None,
    poll_result: dict | None = None,
):
    convos = (
        conversations
        if conversations is not None
        else [{"session_id": "wa_a"}, {"session_id": "wa_b"}, {"session_id": "wa_c"}]
    )
    completed_result = poll_result if poll_result is not None else _FULL_GRAPH_STATE

    @activity.defn(name="build_reengagement_snapshot")
    async def fake_snapshot() -> dict:
        return {"schema_version": 1, "now_ms": 1716700000000, "conversations": convos}

    @activity.defn(name="dispatch_window_strategist")
    async def fake_dispatch(snapshot: dict, run_id: str) -> str:
        tracker.dispatched_input = snapshot
        return "eid-123"

    @activity.defn(name="poll_window_strategist")
    async def fake_poll(execution_id: str) -> dict:
        tracker.polls += 1
        if tracker.polls < 2:  # primer poll: running → el workflow re-pollea
            return {"status": "running", "awaiting": None, "result": None, "error": None}
        return {"status": "completed", "awaiting": None, "result": completed_result, "error": None}

    @activity.defn(name="orchestration.dispatch_event")
    async def fake_dispatch_event(envelope: EventEnvelope) -> None:
        tracker.envelopes.append(envelope)

    return [fake_snapshot, fake_dispatch, fake_poll, fake_dispatch_event]


async def _run(tracker: Tracker, **kw) -> dict:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[ReengagementCycleWorkflow],
            activities=_fakes(tracker, **kw),
        ):
            return await env.client.execute_workflow(
                ReengagementCycleWorkflow.run,
                id="reengagement-cycle-test",
                task_queue=QUEUE,
            )


def test_workflow_module_importa_extract_agent_result() -> None:
    """Canario anti-L-0 (incidente prod 2026-07-10): ruff --fix podó el import
    de `extract_agent_result` (el hook corrió entre el edit del import y el del
    uso) → NameError en el workflow task, que Temporal reintenta INFINITO — el
    run de prod quedó colgado y el test de ciclo solo lo cazaba por timeout de
    300s. Este canario falla en milisegundos si el import vuelve a desaparecer."""
    import src.plugins.reengagement.agent.cycle.workflows.cycle as wf_module

    assert hasattr(wf_module, "extract_agent_result"), (
        "falta `from src.sdk.graphagentskit import extract_agent_result` en el "
        "bloque imports_passed_through (¿ruff lo podó? L-0)"
    )


@pytest.mark.asyncio
async def test_ciclo_emite_un_evento_por_intent() -> None:
    tracker = Tracker()
    summary = await _run(tracker)

    assert tracker.dispatched_input is not None, "debe despachar el snapshot"
    assert tracker.polls >= 2, "re-pollea mientras el run está running"
    assert [e.event_type for e in tracker.envelopes] == [
        "ReengagementDispatchIntentEvent",
        "ReengagementDispatchIntentEvent",
    ]
    assert [e.payload["session_id"] for e in tracker.envelopes] == ["wa_a", "wa_b"]
    assert tracker.envelopes[0].payload["motivo"]
    assert summary["dispatched"] == 2
    assert summary["suppressed"] == 1


@pytest.mark.asyncio
async def test_result_ya_desenvuelto_tambien_emite() -> None:
    """Tolerancia de forma: si la proyección algún día entrega el contrato
    DIRECTO (sin el state alrededor), la extracción no debe romperse."""
    tracker = Tracker()
    summary = await _run(tracker, poll_result=_RESULT)

    assert summary["dispatched"] == 2
    assert len(tracker.envelopes) == 2


@pytest.mark.asyncio
async def test_result_sin_dispatch_termina_visible_sin_emitir() -> None:
    """PM-005: si el compact PODÓ el result (o cambió la forma), el ciclo
    termina `result_missing_dispatch` VISIBLE — nunca 'completed dispatched=0'
    que finge que no había leads."""
    tracker = Tracker()
    summary = await _run(
        tracker, poll_result={"_pruned_keys": ["result"], "payload": {}}
    )

    assert tracker.envelopes == [], "sin dispatch extraíble NO se emite nada"
    assert summary["dispatched"] == 0
    assert summary["run_status"] == "result_missing_dispatch"


@pytest.mark.asyncio
async def test_snapshot_vacio_no_despacha_run() -> None:
    tracker = Tracker()
    summary = await _run(tracker, conversations=[])

    assert tracker.dispatched_input is None, (
        "sin conversaciones NO se paga el cold start de la caja"
    )
    assert tracker.envelopes == []
    assert summary["dispatched"] == 0
