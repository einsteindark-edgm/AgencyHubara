"""ETA manda la FOTO del pedido cuando pasa a "listo" (y a pedido del operador).

El operador sube la foto al pedido desde el panel derecho de Órdenes. Al mover
el pedido a ``ready`` el ETA manda la plantilla ``order_ready_photo_utility_v1``
(foto en el encabezado) en vez del aviso de estado de siempre — dentro o fuera
de la ventana 24h, la plantilla sirve en ambos casos. Sin foto: el aviso de
siempre. Si la plantilla con foto falla (p.ej. aún no aprobada en Meta): el
aviso de siempre, para que el cliente no se quede sin enterarse.

Además el operador puede disparar el envío a mano (señal ``send_ready_photo``),
aunque el ``ready`` ya se haya notificado: el dedup por etapa no aplica.

Activities FAKE por nombre (molde: test_eta_session_signal_with_start.py).
"""
from __future__ import annotations

import asyncio

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.eta.agent.eta.workflows.eta_session import HubaraEtaSessionWorkflow

QUEUE = "queue-eta-photo-test"
SID = "wa_573001234567"


class Tracker:
    def __init__(self, *, has_photo: bool, photo_fails: bool = False) -> None:
        self.has_photo = has_photo
        self.photo_fails = photo_fails
        self.notified: set[tuple[str, str]] = set()
        self.photo_sends: list[str] = []
        self.status_sends: list[str] = []


def _fakes(t: Tracker):
    @activity.defn(name="start_eta_tracking_activity")
    async def start(session_id: str, order_id: str) -> None:
        return None

    @activity.defn(name="claim_eta_notification_activity")
    async def claim(session_id: str, order_id: str, stage: str, tracking_url: str | None = None, shipping_cost: int | None = None):
        if (order_id, stage) in t.notified:
            return None
        return {
            "customer_name": "",
            "order_display_id": "#31",
            "total_label": "$ 10.000",
            "pay_type": "confirmed",
            "payment_confirmed": True,
            "delivery_window": None,
            "items_label": "Velón",
            "in_service_window": False,
            "has_ready_photo": t.has_photo,
        }

    @activity.defn(name="send_ready_photo_activity")
    async def send_photo(session_id: str, order_id: str) -> dict:
        if t.photo_fails:
            raise ApplicationError("132001 template no existe", non_retryable=True)
        t.photo_sends.append(order_id)
        t.notified.add((order_id, "ready"))
        return {"sent": True, "wa_message_id": "wamid.P"}

    @activity.defn(name="send_whatsapp_message_activity")
    async def send_text(session_id: str, message: str) -> None:
        t.status_sends.append(message)

    @activity.defn(name="send_whatsapp_template_activity")
    async def send_template(session_id: str, template: str, variables: dict) -> None:
        t.status_sends.append(template)

    @activity.defn(name="persist_assistant_message_activity")
    async def persist(session_id: str, message: str) -> None:
        return None

    @activity.defn(name="record_eta_notification_activity")
    async def record(session_id: str, order_id: str, stage: str, message: str) -> None:
        t.notified.add((order_id, stage))

    @activity.defn(name="all_trackings_terminal_activity")
    async def all_terminal(session_id: str) -> bool:
        return False

    return [start, claim, send_photo, send_text, send_template, persist, record, all_terminal]


async def _wait_until(pred, *, timeout: float = 15.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not pred():
        if loop.time() > deadline:
            raise AssertionError("timeout esperando la condición")
        await asyncio.sleep(0.05)


def _stage(order_id: str, stage: str) -> dict:
    return {"session_id": SID, "order_id": order_id, "to_stage": stage}


async def _run(tracker: Tracker, scenario):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[HubaraEtaSessionWorkflow],
            activities=_fakes(tracker),
        ):
            handle = await env.client.start_workflow(
                "HubaraEtaSessionWorkflow",
                {"session_id": SID, "order_id": "order_A"},
                id=f"eta-{SID}",
                task_queue=QUEUE,
            )
            await scenario(handle)
            await handle.terminate("test cleanup")


@pytest.mark.asyncio
async def test_ready_with_photo_sends_the_photo_template_instead_of_the_status_notice():
    t = Tracker(has_photo=True)

    async def scenario(handle):
        await handle.signal("notify_stage_change", _stage("order_A", "ready"))
        await _wait_until(lambda: t.photo_sends)
        await asyncio.sleep(0.3)

    await _run(t, scenario)
    assert t.photo_sends == ["order_A"]
    assert t.status_sends == []


@pytest.mark.asyncio
async def test_ready_without_photo_keeps_the_usual_status_notice():
    t = Tracker(has_photo=False)

    async def scenario(handle):
        await handle.signal("notify_stage_change", _stage("order_A", "ready"))
        await _wait_until(lambda: t.status_sends)
        await asyncio.sleep(0.3)

    await _run(t, scenario)
    assert t.photo_sends == []
    assert t.status_sends == ["order_status_utility_v2"]


@pytest.mark.asyncio
async def test_failed_photo_template_falls_back_to_the_status_notice():
    t = Tracker(has_photo=True, photo_fails=True)

    async def scenario(handle):
        await handle.signal("notify_stage_change", _stage("order_A", "ready"))
        await _wait_until(lambda: t.status_sends)
        await asyncio.sleep(0.3)

    await _run(t, scenario)
    assert t.status_sends == ["order_status_utility_v2"]
    assert ("order_A", "ready") in t.notified


@pytest.mark.asyncio
async def test_operator_can_send_the_photo_by_hand_even_after_ready_was_notified():
    t = Tracker(has_photo=True)

    async def scenario(handle):
        await handle.signal("notify_stage_change", _stage("order_A", "ready"))
        await _wait_until(lambda: len(t.photo_sends) == 1)
        # El operador reenvía (p.ej. cambió la foto): no lo frena el dedup de etapa.
        await handle.signal("send_ready_photo", {"session_id": SID, "order_id": "order_A"})
        await _wait_until(lambda: len(t.photo_sends) == 2)
        await asyncio.sleep(0.3)

    await _run(t, scenario)
    assert t.photo_sends == ["order_A", "order_A"]
    assert t.status_sends == []


@pytest.mark.asyncio
async def test_manual_send_on_a_fresh_session_started_by_the_request():
    """signal_with_start sobre una sesión que no existía: arranca, y la señal
    manual manda la foto aunque el pedido nunca pasó por ``notify_stage_change``."""
    t = Tracker(has_photo=True)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[HubaraEtaSessionWorkflow],
            activities=_fakes(t),
        ):
            payload = {"session_id": SID, "order_id": "order_B"}
            handle = await env.client.start_workflow(
                "HubaraEtaSessionWorkflow",
                payload,
                id=f"eta-{SID}",
                task_queue=QUEUE,
                start_signal="send_ready_photo",
                start_signal_args=[payload],
            )
            await _wait_until(lambda: t.photo_sends)
            await asyncio.sleep(0.3)
            await handle.terminate("test cleanup")
    assert t.photo_sends == ["order_B"]
    assert t.status_sends == []
