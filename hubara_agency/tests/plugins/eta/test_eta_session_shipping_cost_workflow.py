"""HubaraEtaSessionWorkflow — "listo" sin monto + valor del envío en "en camino".

Comportamiento (pedido del operador 2026-09-22), verificado sobre lo que el
workflow EMITE (gotcha #1):

  * signal ``ready`` (sin foto) → sale el aviso de siempre, SIN monto.
  * signal ``shipping`` con ``shipping_cost`` + ventana abierta → el texto
    lleva "El valor del envío es $ 12.000".
  * mismo signal fuera de ventana → el template lleva el valor en
    ``status_label``.
  * signal sin ``shipping_cost`` (emisor legacy) → mensaje de siempre.
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
SESSION = "wa_573001112233"


class Tracker:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.templates: list[tuple[str, dict]] = []
        self.recorded: list[tuple[str, str]] = []
        self.claims: list[tuple] = []


def _fakes(tracker: Tracker, *, in_window: bool):
    @activity.defn(name="start_eta_tracking_activity")
    async def fake_start(session_id: str, order_id: str) -> None:
        return None

    @activity.defn(name="claim_eta_notification_activity")
    async def fake_claim(
        session_id: str, order_id: str, stage: str,
        tracking_url: str | None = None, shipping_cost: int | None = None,
    ) -> dict:
        tracker.claims.append((stage, tracking_url, shipping_cost))
        return {
            "customer_name": "Ana",
            "order_display_id": "#9",
            # El envío real ($ 12.000) ya quedó fijado al marcar "en camino":
            # el total vigente lo incluye y el valor del pedido va aparte.
            "total_label": "$ 62.000",
            "total_cop": 62000,
            "order_value_cop": 50000,
            "pay_type": "confirmed",
            "payment_confirmed": False,
            "delivery_window": None,
            "items_label": "Difusor",
            "in_service_window": in_window,
            "has_ready_photo": False,
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


async def _run_with_signals(tracker: Tracker, payloads: list[dict], *, in_window: bool) -> None:
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
            for payload in payloads:
                await handle.signal(HubaraEtaSessionWorkflow.notify_stage_change, payload)
            await handle.result()


@pytest.mark.asyncio
async def test_ready_still_notifies_without_the_amount():
    tracker = Tracker()
    await _run_with_signals(
        tracker,
        [{"to_stage": "ready", "order_id": "order_01HX", "shipping_cost": 12000}],
        in_window=True,
    )

    assert tracker.texts == [
        "¡Buenas noticias Ana! Tu pedido #9 ya está empacado y listo para salir. "
        "Te escribo apenas vaya en camino."
    ]
    assert [s for s, _ in tracker.recorded] == ["ready"]


@pytest.mark.asyncio
async def test_ready_out_of_window_uses_the_plain_status_template():
    tracker = Tracker()
    await _run_with_signals(
        tracker,
        [{"to_stage": "ready", "order_id": "order_01HX", "shipping_cost": 12000}],
        in_window=False,
    )

    assert tracker.texts == []
    assert tracker.templates[0][1]["status_label"] == "Listo para envío"


@pytest.mark.asyncio
async def test_shipping_signal_with_cost_sends_value_in_text():
    tracker = Tracker()
    await _run_with_signals(
        tracker,
        [{"to_stage": "shipping", "order_id": "order_01HX", "shipping_cost": 12000}],
        in_window=True,
    )

    assert tracker.texts == [
        "Tu pedido #9 (Difusor) ya va en camino 🚚.\n"
        "Valor del pedido: $ 50.000\n"
        "Valor del envío: $ 12.000\n"
        "Total: $ 62.000\n"
        "Te aviso cuando esté por llegar."
    ]
    assert tracker.templates == []
    assert tracker.claims == [("shipping", None, 12000)]


@pytest.mark.asyncio
async def test_shipping_signal_with_cost_out_of_window_uses_template_slot():
    tracker = Tracker()
    await _run_with_signals(
        tracker,
        [{"to_stage": "shipping", "order_id": "order_01HX", "shipping_cost": "12000"}],
        in_window=False,
    )

    assert tracker.texts == []
    name, variables = tracker.templates[0]
    assert name == "order_status_utility_v2"
    assert variables["status_label"] == (
        "en camino. Valor del pedido: $ 50.000, valor del envío: $ 12.000, total: $ 62.000"
    )


@pytest.mark.asyncio
async def test_shipping_signal_without_cost_is_legacy_message():
    tracker = Tracker()
    await _run_with_signals(
        tracker,
        [{"to_stage": "shipping", "order_id": "order_01HX", "shipping_cost": None}],
        in_window=True,
    )

    assert tracker.texts == [
        "Tu pedido #9 (Difusor) ya va en camino 🚚. Te aviso cuando esté por llegar."
    ]
    assert tracker.claims == [("shipping", None, None)]
