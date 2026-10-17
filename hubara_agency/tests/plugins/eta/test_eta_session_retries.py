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


# ── Premortem 2026-10-16 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_ambiguous_template_send_is_not_retried() -> None:
    """Un timeout o un 504 de Meta pudo haber entregado el aviso: reintentarlo
    con paciencia le mandaba al cliente N veces «tu pedido va en camino»."""

    @activity.defn(name="claim_eta_notification_activity")
    async def claim(session_id, order_id, stage, tracking_url=None, shipping_cost=None):
        return dict(_FACTS)

    attempts: list[int] = []

    @activity.defn(name="send_whatsapp_template_activity")
    async def ambiguous(session_id: str, template: str, variables: dict) -> None:
        attempts.append(activity.info().attempt)
        raise ApplicationError("timeout: ReadTimeout", type="TemplateSendAmbiguous")

    await _notify_shipping([claim, ambiguous])
    assert attempts == [1]


async def _notify_two(replaced: list) -> Tracker:
    """`shipping` y después `delivered`, en la misma sesión."""
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
            for stage in ("shipping", "delivered"):
                await handle.signal("notify_stage_change", {"session_id": SESSION, "order_id": "order_A", "to_stage": stage})
            await handle.result()
    return tracker


@pytest.mark.asyncio
async def test_a_send_that_keeps_failing_gives_up_and_lets_the_next_notice_out() -> None:
    """Un error que no se arregla solo (una plantilla mal configurada, un token
    vencido) no traba la sesión por horas: se rinde en intentos acotados y el
    aviso siguiente sale."""

    @activity.defn(name="claim_eta_notification_activity")
    async def claim(session_id, order_id, stage, tracking_url=None, shipping_cost=None):
        return {**_FACTS, "stage": stage}

    attempts: dict[str, int] = {}
    sent: list[str] = []

    @activity.defn(name="send_whatsapp_template_activity")
    async def broken_for_shipping(session_id: str, template: str, variables: dict) -> None:
        label = str(variables.get("status_label", ""))
        attempts[label] = attempts.get(label, 0) + 1
        if attempts and len(attempts) == 1:  # el primero (shipping) falla siempre
            raise ApplicationError("http_400: code 100 invalid parameter", type="TemplateSendRetryable")
        sent.append(label)

    await _notify_two([claim, broken_for_shipping])
    assert 5 <= next(iter(attempts.values())) <= 15, attempts
    assert len(sent) == 1, f"el aviso siguiente no salió: {attempts}"


@pytest.mark.asyncio
async def test_a_vault_write_that_keeps_failing_does_not_hold_the_session() -> None:
    """Las escrituras del vault (grabar el aviso) fallan por un bug, no por una
    caída de minutos: pocos intentos y la sesión sigue (no 6 h por aviso)."""
    attempts: list[int] = []

    @activity.defn(name="record_eta_notification_activity")
    async def broken_record(session_id: str, order_id: str, stage: str, message: str) -> None:
        attempts.append(activity.info().attempt)
        raise RuntimeError("metadata.json dañado")

    sent: list[str] = []

    @activity.defn(name="send_whatsapp_template_activity")
    async def send(session_id: str, template: str, variables: dict) -> None:
        sent.append(template)

    @activity.defn(name="claim_eta_notification_activity")
    async def claim(session_id, order_id, stage, tracking_url=None, shipping_cost=None):
        return dict(_FACTS)

    await _notify_two([claim, send, broken_record])
    assert len(sent) == 2, "el segundo aviso quedó esperando al primero"
    assert len(attempts) == 2 * max(attempts), attempts  # dos avisos, los mismos intentos cada uno
    assert max(attempts) <= 5


@pytest.mark.asyncio
async def test_no_eta_activity_expires_while_it_waits_for_a_worker() -> None:
    """`schedule_to_close` cuenta también la espera en la cola: con un worker
    ETA caído más que eso, los avisos vencían sin salir. Antes salían al volver
    el worker: el tope de los reintentos son los intentos, no un reloj."""
    from temporalio.api.enums.v1 import EventType

    tracker = Tracker(has_photo=False)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=QUEUE, workflows=[HubaraEtaSessionWorkflow], activities=_fakes(tracker)):
            handle = await env.client.start_workflow(
                "HubaraEtaSessionWorkflow",
                {"session_id": SESSION, "order_id": "order_A"},
                id=f"eta-{SESSION}",
                task_queue=QUEUE,
            )
            await handle.signal("notify_stage_change", {"session_id": SESSION, "order_id": "order_A", "to_stage": "shipping"})
            await handle.result()
            history = await handle.fetch_history()

    scheduled = {
        e.activity_task_scheduled_event_attributes.activity_type.name: e.activity_task_scheduled_event_attributes
        for e in history.events
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    }
    assert {"claim_eta_notification_activity", "send_whatsapp_template_activity"} <= set(scheduled)
    for name, attrs in scheduled.items():
        # Sin `schedule_to_close` el servidor pone el suyo (años): lo que se
        # prueba es que ningún reloj corto venza la espera en la cola.
        assert attrs.schedule_to_close_timeout.ToTimedelta().days >= 1, name
        assert attrs.retry_policy.maximum_attempts > 0, f"{name}: reintenta sin tope"


@pytest.mark.asyncio
async def test_a_text_notice_whatsapp_did_not_take_goes_out_as_the_template() -> None:
    """Dentro de la ventana el aviso sale como texto. Si WhatsApp no acepta
    ninguna burbuja (la ventana se cerró entre el claim y el envío, Meta caído),
    la activity no lanza: devuelve `[]`. Antes el aviso quedaba grabado como
    enviado y se perdía; ahora sale por la plantilla (sirve dentro y fuera de
    la ventana y reintenta)."""

    @activity.defn(name="claim_eta_notification_activity")
    async def claim_in_window(session_id, order_id, stage, tracking_url=None, shipping_cost=None):
        return {**_FACTS, "in_service_window": True}

    @activity.defn(name="send_whatsapp_message_activity")
    async def rejected_text(session_id: str, message: str, decided_by_engine: bool = False) -> list:
        return []

    templates: list[str] = []

    @activity.defn(name="send_whatsapp_template_activity")
    async def send_template(session_id: str, template: str, variables: dict) -> None:
        templates.append(template)

    tracker = await _notify_shipping([claim_in_window, rejected_text, send_template])
    assert templates == ["order_status_utility_v2"], "el aviso no salió por la plantilla"
    assert tracker.status_sends == [], tracker.status_sends
