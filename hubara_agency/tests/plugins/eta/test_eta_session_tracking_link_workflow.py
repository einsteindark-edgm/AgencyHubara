"""HubaraEtaSessionWorkflow — el link de guía del signal llega al WhatsApp.

Verifica COMPORTAMIENTO (gotcha #1 del CLAUDE.md: no basta con que el
schema admita `tracking_url` — el workflow tiene que EMITIRLO):

  * signal `notify_stage_change` con `tracking_url` + ventana abierta →
    `send_whatsapp_message_activity` recibe el texto con la URL al final.
  * mismo signal fuera de ventana → el template `order_status_utility_v2`
    lleva la URL en `status_label`.
  * signal sin `tracking_url` (emisor legacy) → mensaje de siempre.

WorkflowEnvironment time-skipping + activities fake (R-DET). El workflow
termina solo cuando el idle de 7 días se saltea.
"""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.eta.agent.eta.contracts import EtaSessionInput
from src.plugins.eta.agent.eta.workflows.eta_session import HubaraEtaSessionWorkflow
from src.sdk import get_task_queue

QUEUE = get_task_queue("eta", "eta")
URL = "https://www.interrapidisimo.com/sigue-tu-envio/?guia=700012345678"
SESSION = "wa_573001112233"


class Tracker:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.templates: list[tuple[str, dict]] = []
        self.recorded: list[tuple[str, str]] = []


def _fakes(tracker: Tracker, *, in_window: bool):
    @activity.defn(name="start_eta_tracking_activity")
    async def fake_start(session_id: str, order_id: str) -> None:
        return None

    @activity.defn(name="claim_eta_notification_activity")
    async def fake_claim(session_id: str, order_id: str, stage: str) -> dict:
        return {
            "customer_name": "Ana",
            "order_display_id": "#9",
            "total_label": "",
            "pay_type": "confirmed",
            "payment_confirmed": False,
            "delivery_window": None,
            "items_label": "Difusor",
            "in_service_window": in_window,
        }

    @activity.defn(name="send_whatsapp_message_activity")
    async def fake_send(session_id: str, message: str) -> None:
        tracker.texts.append(message)

    @activity.defn(name="send_whatsapp_template_activity")
    async def fake_template(session_id: str, template_name: str, variables: dict) -> dict:
        tracker.templates.append((template_name, variables))
        return {"wa_message_id": "wamid-1", "ok": True, "error": None}

    @activity.defn(name="persist_assistant_message_activity")
    async def fake_persist(session_id: str, message: str) -> None:
        return None

    @activity.defn(name="record_eta_notification_activity")
    async def fake_record(session_id: str, order_id: str, stage: str, agent_msg: str) -> None:
        tracker.recorded.append((stage, agent_msg))

    @activity.defn(name="all_trackings_terminal_activity")
    async def fake_terminal(session_id: str) -> bool:
        return False

    return [
        fake_start, fake_claim, fake_send, fake_template,
        fake_persist, fake_record, fake_terminal,
    ]


async def _run_with_signal(tracker: Tracker, payload: dict, *, in_window: bool) -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[HubaraEtaSessionWorkflow],
            activities=_fakes(tracker, in_window=in_window),
        ):
            handle = await env.client.start_workflow(
                HubaraEtaSessionWorkflow.run,
                EtaSessionInput(session_id=SESSION, order_id="order_01HX", to_stage=""),
                id=f"eta-{SESSION}",
                task_queue=QUEUE,
            )
            await handle.signal(HubaraEtaSessionWorkflow.notify_stage_change, payload)
            # Idle de 7 días saltado por el test server → el run termina.
            await handle.result()


@pytest.mark.asyncio
async def test_shipping_signal_with_tracking_url_sends_link_in_text():
    tracker = Tracker()
    await _run_with_signal(
        tracker,
        {"to_stage": "shipping", "order_id": "order_01HX", "tracking_url": URL},
        in_window=True,
    )

    assert tracker.texts == [
        "Tu pedido #9 (Difusor) ya va en camino 🚚. Te aviso cuando esté por llegar."
        f"\n\nPuedes seguir tu envío aquí: {URL}"
    ]
    assert tracker.templates == []
    assert tracker.recorded[0][0] == "shipping" and URL in tracker.recorded[0][1]


@pytest.mark.asyncio
async def test_shipping_signal_with_tracking_url_out_of_window_uses_template_slot():
    tracker = Tracker()
    await _run_with_signal(
        tracker,
        {"to_stage": "shipping", "order_id": "order_01HX", "tracking_url": URL},
        in_window=False,
    )

    assert tracker.texts == []
    assert len(tracker.templates) == 1
    name, variables = tracker.templates[0]
    assert name == "order_status_utility_v2"
    assert variables["status_label"] == f"en camino. Sigue tu envío aquí: {URL}"


@pytest.mark.asyncio
async def test_shipping_signal_without_tracking_url_is_legacy_message():
    tracker = Tracker()
    await _run_with_signal(
        tracker,
        {"to_stage": "shipping", "order_id": "order_01HX"},
        in_window=True,
    )

    assert tracker.texts == [
        "Tu pedido #9 (Difusor) ya va en camino 🚚. Te aviso cuando esté por llegar."
    ]
