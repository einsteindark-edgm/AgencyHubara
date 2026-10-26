"""Continue-as-new del ETA (auditoría de Temporal, 2026-10-15).

El run que nace de un continue-as-new trae el `turn_count` acumulado (50); el
tope lo comparaba con ese contador → desde la notificación 50, cada aviso hacía
continue-as-new y repetía el arranque del tracking. El tope cuenta los avisos
del run.
"""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.eta.agent.eta.workflows.eta_session import _CONTINUE_AS_NEW_AFTER_TURNS, HubaraEtaSessionWorkflow
from tests.plugins.eta.test_eta_ready_photo_workflow import QUEUE, SID, Tracker, _fakes, _stage


@pytest.mark.asyncio
async def test_a_run_born_from_continue_as_new_does_not_continue_again_after_one_notice() -> None:
    tracker = Tracker(has_photo=False)
    starts: list[str] = []

    @activity.defn(name="start_eta_tracking_activity")
    async def counting_start(session_id: str, order_id: str) -> None:
        starts.append(order_id)

    acts = [a for a in _fakes(tracker) if a.__temporal_activity_definition.name != "start_eta_tracking_activity"]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=QUEUE, workflows=[HubaraEtaSessionWorkflow], activities=[*acts, counting_start]):
            # El run que dejó el continue-as-new anterior: 50 avisos acumulados.
            handle = await env.client.start_workflow(
                "HubaraEtaSessionWorkflow",
                {"session_id": SID, "order_id": "order_A", "turn_count": _CONTINUE_AS_NEW_AFTER_TURNS},
                id=f"eta-{SID}",
                task_queue=QUEUE,
            )
            await handle.signal("notify_stage_change", _stage("order_A", "shipping"))
            # Una semana sin eventos cierra la sesión.
            await handle.result()

    assert tracker.status_sends, "el aviso no salió"
    assert starts == ["order_A"], f"el run hizo continue-as-new tras UN aviso y repitió el arranque: {starts}"
