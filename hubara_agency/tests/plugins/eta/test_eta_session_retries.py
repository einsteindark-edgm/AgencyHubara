"""El aviso de estado sobrevive a una caída de minutos (auditoría de Temporal, 2026-10-15).

El ETA se rendía en ~3 s: tres intentos para leer el pedido (Medusa) y tres
para mandar la plantilla, y el loop se tragaba la excepción como «no fatal».
Un Medusa o un WhatsApp caídos un par de minutos = el cliente nunca se entera
de que su pedido salió. Ahora reintenta con espera creciente (tope de minutos)
durante horas; un error que Meta marca como definitivo sigue cortando en el
primer intento.
"""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.eta.agent.eta.workflows.eta_session import HubaraEtaSessionWorkflow
from tests.plugins.eta.test_eta_ready_photo_workflow import QUEUE, Tracker, _fakes

SESSION = "wa_eta_retries"
_FACTS = {
    "customer_name": "",
    "order_display_id": "#31",
    "total_label": "$ 10.000",
    "pay_type": "confirmed",
    "payment_confirmed": True,
    "delivery_window": None,
    "items_label": "Velón",
    "in_service_window": False,
    "has_ready_photo": False,
}


async def _notify_shipping(replaced: list) -> Tracker:
    """Un aviso de `shipping` fuera de ventana (plantilla); la sesión cierra sola
    tras la semana sin eventos (el servidor de pruebas salta el tiempo)."""
    tracker = Tracker(has_photo=False)
    names = {a.__temporal_activity_definition.name for a in replaced}
    acts = [a for a in _fakes(tracker) if a.__temporal_activity_definition.name not in names] + replaced
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=QUEUE, workflows=[HubaraEtaSessionWorkflow], activities=acts):
            handle = await env.client.start_workflow(
                "HubaraEtaSessionWorkflow",
                {"session_id": SESSION, "order_id": "order_A"},
                id=f"eta-{SESSION}",
                task_queue=QUEUE,
            )
            await handle.signal("notify_stage_change", {"session_id": SESSION, "order_id": "order_A", "to_stage": "shipping"})
            await handle.result()
    return tracker


@pytest.mark.asyncio
async def test_the_notice_survives_medusa_down_for_a_few_minutes() -> None:
    @activity.defn(name="claim_eta_notification_activity")
    async def flaky_claim(session_id, order_id, stage, tracking_url=None, shipping_cost=None):
        if activity.info().attempt <= 6:  # ~2 minutos de reintentos
            raise RuntimeError("Medusa no responde")
        return dict(_FACTS)

    sent: list[str] = []

    @activity.defn(name="send_whatsapp_template_activity")
    async def send_template(session_id: str, template: str, variables: dict) -> None:
        sent.append(template)

    await _notify_shipping([flaky_claim, send_template])
    assert sent == ["order_status_utility_v2"], "el aviso se perdió con Medusa caído unos minutos"


@pytest.mark.asyncio
async def test_the_notice_survives_whatsapp_down_for_a_few_minutes() -> None:
    @activity.defn(name="claim_eta_notification_activity")
    async def claim(session_id, order_id, stage, tracking_url=None, shipping_cost=None):
        return dict(_FACTS)

    sent: list[str] = []

    @activity.defn(name="send_whatsapp_template_activity")
    async def flaky_send(session_id: str, template: str, variables: dict) -> None:
        if activity.info().attempt <= 5:
            raise RuntimeError("graph.facebook.com 503")
        sent.append(template)

    await _notify_shipping([claim, flaky_send])
    assert sent == ["order_status_utility_v2"], "el aviso se perdió con WhatsApp caído unos minutos"


@pytest.mark.asyncio
async def test_a_definitive_meta_rejection_is_not_retried() -> None:
    @activity.defn(name="claim_eta_notification_activity")
    async def claim(session_id, order_id, stage, tracking_url=None, shipping_cost=None):
        return dict(_FACTS)

    attempts: list[int] = []

    @activity.defn(name="send_whatsapp_template_activity")
    async def rejected(session_id: str, template: str, variables: dict) -> None:
        attempts.append(activity.info().attempt)
        raise ApplicationError("132001 template no existe", non_retryable=True)

    await _notify_shipping([claim, rejected])
    assert attempts == [1]
