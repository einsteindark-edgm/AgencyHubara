"""Workflow-level guards del contrato que `signal_with_start` en `preparing`
necesita de `HubaraEtaSessionWorkflow` (cambio de diseño 2026-09-08).

1. Arranque FRESCO vía signal_with_start: Temporal entrega el start-signal
   (`notify_stage_change` con el seed `preparing`) Y el run input trae el mismo
   `to_stage`. El workflow encola los dos, pero el cliente recibe UNA sola
   notificación (dedup por `notified_stages` entre claim y record).
2. Sesión VIVA que recibe `preparing` de un pedido NUEVO: lo notifica como un
   stage más y sigue corriendo (no hay run Terminated, no se pierde el
   seguimiento del pedido anterior).

Activities FAKE registradas por nombre (molde:
tests/plugins/order_sentinel/test_cycle_workflow.py). El fake de claim/record
reproduce la semántica real de dedup ya unit-testeada en test_eta_agent.py
(`test_claim_dedups_already_notified`, `test_record_notification_appends_event_and_dedups`).
"""
from __future__ import annotations

import asyncio

import pytest
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.eta.agent.eta.workflows.eta_session import HubaraEtaSessionWorkflow

QUEUE = "queue-eta-test"
SID = "wa_573000000000"


class Tracker:
    def __init__(self) -> None:
        self.tracking_started: list[str] = []
        self.claims: list[tuple[str, str]] = []
        self.notified: set[tuple[str, str]] = set()
        self.sent: list[str] = []


def _fakes(tracker: Tracker):
    @activity.defn(name="start_eta_tracking_activity")
    async def fake_start_tracking(session_id: str, order_id: str) -> None:
        tracker.tracking_started.append(order_id)

    @activity.defn(name="claim_eta_notification_activity")
    async def fake_claim(session_id: str, order_id: str, stage: str) -> dict | None:
        tracker.claims.append((order_id, stage))
        if (order_id, stage) in tracker.notified:
            return None  # dedup real: stage ya en notified_stages del pedido
        return {
            "customer_name": "",
            "order_display_id": order_id,
            "total_label": "$ 10.000",
            "pay_type": "confirmed",
            "payment_confirmed": True,
            "delivery_window": None,
            "items_label": "Velón",
            "in_service_window": True,
        }

    @activity.defn(name="send_whatsapp_message_activity")
    async def fake_send(session_id: str, message: str) -> None:
        tracker.sent.append(message)

    @activity.defn(name="send_whatsapp_template_activity")
    async def fake_send_template(session_id: str, template: str, variables: dict) -> None:
        tracker.sent.append(f"[template {template}] {variables}")

    @activity.defn(name="persist_assistant_message_activity")
    async def fake_persist(session_id: str, message: str) -> None:
        return None

    @activity.defn(name="record_eta_notification_activity")
    async def fake_record(session_id: str, order_id: str, stage: str, message: str) -> None:
        tracker.notified.add((order_id, stage))

    @activity.defn(name="all_trackings_terminal_activity")
    async def fake_all_terminal(session_id: str) -> bool:
        return False

    return [
        fake_start_tracking,
        fake_claim,
        fake_send,
        fake_send_template,
        fake_persist,
        fake_record,
        fake_all_terminal,
    ]


async def _wait_until(pred, *, timeout: float = 15.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not pred():
        if loop.time() > deadline:
            raise AssertionError("timeout esperando la condición")
        await asyncio.sleep(0.05)


def _payload(order_id: str, stage: str) -> dict:
    # Mismo dict que arma el dispatcher desde input_mapping (start input Y signal).
    return {"session_id": SID, "order_id": order_id, "to_stage": stage}


@pytest.mark.asyncio
async def test_signal_with_start_fresh_sends_the_seed_notification_once():
    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[HubaraEtaSessionWorkflow],
            activities=_fakes(tracker),
        ):
            handle = await env.client.start_workflow(
                "HubaraEtaSessionWorkflow",
                _payload("order_A", "preparing"),
                id=f"eta-{SID}",
                task_queue=QUEUE,
                start_signal="notify_stage_change",
                start_signal_args=[_payload("order_A", "preparing")],
            )
            await _wait_until(lambda: ("order_A", "preparing") in tracker.notified)
            # El seed llega dos veces (start-signal + run input): dos claims,
            # UN solo envío al cliente.
            await _wait_until(lambda: len(tracker.claims) >= 2)
            await asyncio.sleep(0.3)
            assert tracker.tracking_started == ["order_A"]
            assert tracker.claims == [("order_A", "preparing"), ("order_A", "preparing")]
            assert len(tracker.sent) == 1, tracker.sent
            assert "order_A" in tracker.sent[0]
            assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
            await handle.terminate("test cleanup")


@pytest.mark.asyncio
async def test_live_session_absorbs_preparing_of_a_new_order_and_keeps_running():
    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[HubaraEtaSessionWorkflow],
            activities=_fakes(tracker),
        ):
            handle = await env.client.start_workflow(
                "HubaraEtaSessionWorkflow",
                _payload("order_A", "preparing"),
                id=f"eta-{SID}",
                task_queue=QUEUE,
            )
            await _wait_until(lambda: ("order_A", "preparing") in tracker.notified)
            first_run_id = (await handle.describe()).run_id

            # Segundo pedido del mismo cliente entra en preparación: lo que
            # ahora hace el dispatcher con signal_with_start sobre la sesión viva.
            await handle.signal("notify_stage_change", _payload("order_B", "preparing"))
            await _wait_until(lambda: ("order_B", "preparing") in tracker.notified)

            # Y el pedido B avanza — misma sesión, mismo run.
            await handle.signal("notify_stage_change", _payload("order_B", "delivered"))
            await _wait_until(lambda: ("order_B", "delivered") in tracker.notified)
            await asyncio.sleep(0.3)

            desc = await handle.describe()
            assert desc.status == WorkflowExecutionStatus.RUNNING, (
                "la sesión ETA viva debe seguir corriendo (no Terminated / no reemplazo)"
            )
            assert desc.run_id == first_run_id
            assert len(tracker.sent) == 3, tracker.sent
            assert "order_B" in tracker.sent[1] and "order_B" in tracker.sent[2]
            # El pedido A sigue trackeado: nadie lo reinició ni lo cerró.
            assert tracker.tracking_started == ["order_A"]
            await handle.terminate("test cleanup")
