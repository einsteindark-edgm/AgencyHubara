"""Workflow-level tests de PostSaleReturnWorkflow (fase roja TDD).

One-shot diario (lo dispara un Temporal Schedule): scan → una activity por
sesión candidata → summary. R-DET: cero I/O en el workflow. Una sesión que
falla (tras retries) NO tumba el ciclo — cuenta como `failed` y sigue con
las demás (el próximo ciclo la reintenta).

Activities FAKE registradas POR NOMBRE (molde:
tests/plugins/order_sentinel/test_cycle_workflow.py).
"""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.post_sale_return.workflows import (
    PostSaleReturnWorkflow,
)

QUEUE = "queue-post-sale-return"


class Tracker:
    def __init__(self) -> None:
        self.returned_calls: list[str] = []


def _fakes(tracker: Tracker, *, sessions: list[str], results: dict[str, str]):
    @activity.defn(name="scan_post_sale_human_sessions")
    async def fake_scan() -> list[str]:
        return sessions

    @activity.defn(name="return_post_sale_session_to_sales")
    async def fake_return(session_id: str) -> str:
        tracker.returned_calls.append(session_id)
        result = results[session_id]
        if result == "boom":
            raise ApplicationError("vault roto", non_retryable=True)
        return result

    return [fake_scan, fake_return]


async def _run(tracker: Tracker, **kw) -> dict:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[PostSaleReturnWorkflow],
            activities=_fakes(tracker, **kw),
        ):
            return await env.client.execute_workflow(
                PostSaleReturnWorkflow.run,
                id="post-sale-return-test",
                task_queue=QUEUE,
            )


@pytest.mark.asyncio
async def test_ciclo_devuelve_las_candidatas_y_reporta_summary():
    tracker = Tracker()
    summary = await _run(
        tracker,
        sessions=["wa_1", "wa_2", "wa_3", "wa_4", "wa_5", "wa_6"],
        results={
            "wa_1": "returned",
            "wa_2": "skipped_robot_running",
            "wa_3": "returned",
            "wa_4": "skipped_state_changed",
            # Pedido aún en proceso / estado inverificable: quedan en humano
            # y el summary los reporta por separado (nada silencioso).
            "wa_5": "skipped_order_not_delivered",
            "wa_6": "skipped_order_state_unknown",
        },
    )
    assert tracker.returned_calls == ["wa_1", "wa_2", "wa_3", "wa_4", "wa_5", "wa_6"]
    assert summary == {
        "scanned": 6,
        "returned": 2,
        "skipped_robot_running": 1,
        "skipped_state_changed": 1,
        "skipped_order_not_delivered": 1,
        "skipped_order_state_unknown": 1,
        "failed": 0,
        "returned_sessions": ["wa_1", "wa_3"],
    }


@pytest.mark.asyncio
async def test_scan_vacio_no_llama_activities_per_session():
    tracker = Tracker()
    summary = await _run(tracker, sessions=[], results={})
    assert tracker.returned_calls == []
    assert summary["scanned"] == 0
    assert summary["returned"] == 0


@pytest.mark.asyncio
async def test_una_sesion_fallida_no_tumba_el_ciclo():
    tracker = Tracker()
    summary = await _run(
        tracker,
        sessions=["wa_1", "wa_2", "wa_3"],
        results={"wa_1": "returned", "wa_2": "boom", "wa_3": "returned"},
    )
    # La fallida cuenta como failed y las demás igual se procesan.
    assert tracker.returned_calls == ["wa_1", "wa_2", "wa_3"]
    assert summary["returned"] == 2
    assert summary["failed"] == 1
    assert summary["returned_sessions"] == ["wa_1", "wa_3"]
