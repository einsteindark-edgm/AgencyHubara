"""La respuesta del formulario de calificación no es «datos de envío».

Reseñas tras la entrega (2026-10-13): el formulario `review_v1` vuelve como
`nfm_reply` igual que el de envío. Hasta hoy toda respuesta de Flow se leía
como «[datos de envío recibidos]» y despertaba al bot de ventas. Contrato:
  * el texto del chat dice lo que el cliente calificó (el operador lo ve);
  * se emite `ReviewSubmittedEvent` para el plugin de reseñas (el gracias, el
    cupón y la reseña los hace él, de forma determinista — sin LLM);
  * el bot de ventas NO toma el turno y la espera del formulario de envío
    no se toca;
  * la respuesta del formulario de envío sigue como siempre.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

import src.plugins.chats.agent.sales.use_cases.ingest_inbound_message as ingest_mod
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.translate import translate_to_effective_text
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from tests.plugins.chats.test_ingest_window_from_customer_time import (
    _SID,
    _History,
    _LoadOrStart,
    _Metadata,
    _factory,
)

_NOW_MS = 1_791_000_000_000


def _review_reply(**response: Any) -> WhatsAppMessage:
    body = {
        "flow_token": "review:order_01JABC:n1",
        "rating": "4",
        "comment": "Muy linda, el aroma suave",
        "publish_consent": True,
        **response,
    }
    return WhatsAppMessage(
        message_id="wamid.REVIEW1",
        from_number=_SID.removeprefix("wa_"),
        phone_number_id="PID",
        text=None,
        media=None,
        timestamp=str(_NOW_MS // 1000),
        msg_type="interactive",
        interactive={"type": "nfm_reply", "name": "flow", "body": "Sent", "response_json": body},
    )


@pytest.mark.asyncio
async def test_translate_reads_a_review_reply_as_a_rating_not_shipping_data():
    effective = await translate_to_effective_text(_review_reply())
    assert effective.structured_payload["kind"] == "review_reply"
    assert effective.text.startswith("[calificación recibida]")
    assert "★★★★☆" in effective.text
    assert "Muy linda" in effective.text
    assert "envío" not in effective.text


@pytest.mark.asyncio
async def test_translate_keeps_the_shipping_form_reply_as_before():
    effective = await translate_to_effective_text(_review_reply(flow_token="shipping_wa_573001234567_1"))
    assert effective.structured_payload["kind"] == "nfm_reply"
    assert effective.text.startswith("[datos de envío recibidos]")


def _started(envelope: Any) -> Any:
    """Lo que devuelve el dispatcher cuando arrancó el workflow de reseñas."""
    from src.platform.orchestration.dispatcher import DispatchedTransition, DispatchResult

    return DispatchResult(
        source_plugin="chats", source_worker="sales", event_type=envelope.event_type,
        matches=[DispatchedTransition(
            transition_id="review_submitted_starts_reviews", via="start_workflow",
            target_plugin="reviews", target_worker="requests", target_workflow="ReviewSubmissionWorkflow",
            workflow_id="review-submit-x", outcome="started",
        )],
    )


async def _drain(use_case: IngestInboundMessage) -> None:
    """Los reintentos y el re-despacho corren en segundo plano."""
    for _ in range(3):
        await asyncio.gather(*list(getattr(use_case, "_background", ())))
        await asyncio.sleep(0)


class _Dispatch:
    def __init__(self) -> None:
        self.envelopes: list[Any] = []

    async def record(self, envelope: Any, client: Any) -> Any:
        self.envelopes.append(envelope)
        return _started(envelope)


@pytest.mark.asyncio
async def test_a_review_reply_goes_to_the_reviews_plugin_and_not_to_the_sales_bot(monkeypatch):
    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW_MS)
    dispatch = _Dispatch()
    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", dispatch.record)
    history, bot, metadata = _History(), _LoadOrStart(), _Metadata()
    metadata.store[_SID] = {
        "active_route": "humano",
        "shipping_flow_awaiting_reply_since_ms": _NOW_MS - 1000,
        "episodes": [{"episode_id": "ep_003", "started_at_ms": _NOW_MS - 10_000, "closed_at_ms": None}],
    }
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=bot,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
        temporal_client_factory=_factory,  # type: ignore[arg-type]
    )

    await use_case.execute(_review_reply())
    for _ in range(6):
        await asyncio.sleep(0)

    assert bot.calls == []
    assert history.events and history.events[0][1].startswith("[calificación recibida]")
    reviews = [e for e in dispatch.envelopes if e.event_type == "ReviewSubmittedEvent"]
    assert len(reviews) == 1
    payload = reviews[0].payload
    assert payload["session_id"] == _SID
    assert payload["wa_message_id"] == "wamid.REVIEW1"
    assert '"rating": "4"' in payload["response_json"]
    assert metadata.read(_SID)["shipping_flow_awaiting_reply_since_ms"] == _NOW_MS - 1000


@pytest.mark.asyncio
async def test_a_review_after_a_closed_purchase_does_not_reopen_the_conversation(monkeypatch):
    """Revisión 2026-10-13 (H2): la calificación de quien ya compró no abre un
    episodio NO_ETIQUETADO (lo volvería «lead frío» para la reactivación) ni
    programa el watchdog; sí reabre la ventana (el gracias la necesita)."""
    from src.platform.whatsapp.window import SERVICE_WINDOW_MS

    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW_MS)
    dispatch = _Dispatch()
    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", dispatch.record)
    history, bot, metadata = _History(), _LoadOrStart(), _Metadata()
    closed = [{"episode_id": "ep_001", "started_at_ms": _NOW_MS - 9 * 86_400_000,
               "closed_at_ms": _NOW_MS - 3 * 86_400_000, "closing_tag": "COMPRA_EXITOSA"}]
    metadata.store[_SID] = {"active_route": "ventas", "tag": "COMPRA_EXITOSA", "episodes": closed}
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=bot,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
        temporal_client_factory=_factory,  # type: ignore[arg-type]
    )

    await use_case.execute(_review_reply())
    for _ in range(6):
        await asyncio.sleep(0)

    after = metadata.read(_SID)
    assert after["episodes"] == closed
    assert after["tag"] == "COMPRA_EXITOSA"
    assert after["service_window_expires_at_ms"] == _NOW_MS + SERVICE_WINDOW_MS
    assert [e.event_type for e in dispatch.envelopes] == ["ReviewSubmittedEvent"]
    assert bot.calls == []
    assert history.events[0][1].startswith("[calificación recibida]")


@pytest.mark.asyncio
async def test_a_review_reply_without_a_message_id_gets_its_own_workflow_id(monkeypatch):
    """L6: sin wamid el id del workflow no puede ser `review-submit-` para todos."""
    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW_MS)
    dispatch = _Dispatch()
    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", dispatch.record)
    metadata = _Metadata()
    metadata.store[_SID] = {"active_route": "humano", "episodes": []}
    use_case = IngestInboundMessage(
        history_store=_History(), load_session=_LoadOrStart(), metadata_store=metadata,  # type: ignore[arg-type]
        temporal_client_factory=_factory,  # type: ignore[arg-type]
    )
    message = _review_reply()
    object.__setattr__(message, "message_id", "")

    await use_case.execute(message)

    [review] = [e for e in dispatch.envelopes if e.event_type == "ReviewSubmittedEvent"]
    assert review.payload["wa_message_id"].startswith(_SID)


@pytest.mark.asyncio
async def test_a_transient_dispatch_failure_is_retried(monkeypatch):
    """M1: perder la calificación es perder el cupón prometido."""
    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW_MS)
    monkeypatch.setattr(ingest_mod, "_REVIEW_DISPATCH_BACKOFF_S", (0, 0, 0))
    attempts: list[str] = []

    async def flaky(envelope, client):
        attempts.append(envelope.event_type)
        if len(attempts) < 3:
            raise RuntimeError("temporal unavailable")
        return _started(envelope)

    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", flaky)
    metadata = _Metadata()
    metadata.store[_SID] = {"active_route": "humano", "episodes": []}
    use_case = IngestInboundMessage(
        history_store=_History(), load_session=_LoadOrStart(), metadata_store=metadata,  # type: ignore[arg-type]
        temporal_client_factory=_factory,  # type: ignore[arg-type]
    )

    await use_case.execute(_review_reply())
    await _drain(use_case)

    assert attempts == ["ReviewSubmittedEvent"] * 3
    assert "pending_review_submissions" not in metadata.read(_SID)


@pytest.mark.asyncio
async def test_when_dispatch_keeps_failing_the_rating_is_kept_for_recovery(monkeypatch):
    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW_MS)
    monkeypatch.setattr(ingest_mod, "_REVIEW_DISPATCH_BACKOFF_S", (0, 0, 0))

    async def down(envelope, client):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", down)
    metadata = _Metadata()
    metadata.store[_SID] = {"active_route": "humano", "episodes": []}
    use_case = IngestInboundMessage(
        history_store=_History(), load_session=_LoadOrStart(), metadata_store=metadata,  # type: ignore[arg-type]
        temporal_client_factory=_factory,  # type: ignore[arg-type]
    )

    await use_case.execute(_review_reply())
    await _drain(use_case)

    [pending] = metadata.read(_SID)["pending_review_submissions"]
    assert pending["wa_message_id"] == "wamid.REVIEW1"
    assert '"rating": "4"' in pending["response_json"]


# --- Premortem C3: lo que no llegó al plugin de reseñas queda guardado y se
# vuelve a despachar ------------------------------------------------------------


def _review_use_case(metadata: Any, history: Any = None, bot: Any = None) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=history or _History(), load_session=bot or _LoadOrStart(),  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
        temporal_client_factory=_factory,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "unhandled",
    [{"no_matches": True}, {"skipped_disabled": ["reviews/requests"]}],
    ids=["sin_transicion", "reviews_apagado"],
)
@pytest.mark.asyncio
async def test_a_rating_nobody_picked_up_is_parked_not_lost(monkeypatch, unhandled):
    """Despachar sin que arranque nada (sin transición, o el plugin apagado)
    NO es un éxito: la calificación trae el cupón prometido."""
    from src.platform.orchestration.dispatcher import DispatchResult

    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW_MS)
    monkeypatch.setattr(ingest_mod, "_REVIEW_DISPATCH_BACKOFF_S", (0, 0, 0))

    async def nobody(envelope, client):
        return DispatchResult(source_plugin="chats", source_worker="sales",
                              event_type=envelope.event_type, **unhandled)

    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", nobody)
    metadata = _Metadata()
    metadata.store[_SID] = {"active_route": "ventas", "episodes": []}
    use_case = _review_use_case(metadata)

    await use_case.execute(_review_reply())
    await _drain(use_case)

    [pending] = metadata.read(_SID)["pending_review_submissions"]
    assert pending["wa_message_id"] == "wamid.REVIEW1"


@pytest.mark.asyncio
async def test_the_webhook_does_not_sleep_through_the_retries(monkeypatch):
    """Los mensajes que vienen detrás en el mismo webhook no esperan los
    reintentos: el primer intento va en línea y el resto en segundo plano."""
    import time as _time

    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW_MS)

    async def down(envelope, client):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", down)
    metadata = _Metadata()
    metadata.store[_SID] = {"active_route": "ventas", "episodes": []}
    use_case = _review_use_case(metadata)

    started = _time.monotonic()
    await use_case.execute(_review_reply())
    elapsed = _time.monotonic() - started

    assert elapsed < 0.4
    # Guardada desde antes de reintentar: un reinicio a mitad no la pierde.
    assert metadata.read(_SID)["pending_review_submissions"][0]["wa_message_id"] == "wamid.REVIEW1"
    monkeypatch.setattr(ingest_mod, "_REVIEW_DISPATCH_BACKOFF_S", (0, 0, 0))
    await _drain(use_case)


@pytest.mark.asyncio
async def test_a_parked_rating_is_dispatched_again_with_the_next_message(monkeypatch):
    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW_MS)
    dispatch = _Dispatch()
    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", dispatch.record)
    metadata = _Metadata()
    metadata.store[_SID] = {
        "active_route": "ventas",
        "episodes": [{"episode_id": "ep_001", "started_at_ms": _NOW_MS - 10_000, "closed_at_ms": None}],
        "pending_review_submissions": [
            {"wa_message_id": "wamid.REVIEW0", "response_json": '{"rating": "5"}', "received_at_ms": _NOW_MS - 9_000}
        ],
    }
    use_case = _review_use_case(metadata)

    await use_case.execute(WhatsAppMessage(
        message_id="wamid.HOLA", from_number=_SID.removeprefix("wa_"), phone_number_id="PID",
        text="Hola, ¿ya les llegó mi calificación?", media=None, timestamp=str(_NOW_MS // 1000),
    ))
    await _drain(use_case)

    [review] = [e for e in dispatch.envelopes if e.event_type == "ReviewSubmittedEvent"]
    assert review.payload["wa_message_id"] == "wamid.REVIEW0"
    assert review.payload["response_json"] == '{"rating": "5"}'
    assert not metadata.read(_SID).get("pending_review_submissions")


@pytest.mark.asyncio
async def test_a_rating_that_stays_parked_is_an_error_with_the_count(monkeypatch):
    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW_MS)
    errors: list[tuple[str, dict]] = []

    class _Log:
        def __getattr__(self, name):
            def _record(event, **fields):
                if name == "error":
                    errors.append((event, fields))
            return _record

    monkeypatch.setattr(ingest_mod, "logger", _Log())

    async def down(envelope, client):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", down)
    metadata = _Metadata()
    metadata.store[_SID] = {
        "active_route": "ventas",
        "episodes": [{"episode_id": "ep_001", "started_at_ms": _NOW_MS - 10_000, "closed_at_ms": None}],
        "pending_review_submissions": [
            {"wa_message_id": "wamid.REVIEW0", "response_json": "{}", "received_at_ms": _NOW_MS - 9_000}
        ],
    }
    use_case = _review_use_case(metadata)

    await use_case.execute(WhatsAppMessage(
        message_id="wamid.HOLA", from_number=_SID.removeprefix("wa_"), phone_number_id="PID",
        text="Hola", media=None, timestamp=str(_NOW_MS // 1000),
    ))
    await _drain(use_case)

    assert metadata.read(_SID)["pending_review_submissions"][0]["wa_message_id"] == "wamid.REVIEW0"
    parked = [fields for event, fields in errors if event == "review_submissions_parked"]
    assert parked and parked[-1]["parked"] == 1


@pytest.mark.asyncio
async def test_the_composed_webhook_ingest_can_reach_the_reviews_plugin(monkeypatch):
    """Gotcha #1 (tests verdes, feature muerta): el ingest que arma la
    composición NO tenía fábrica de Temporal — toda calificación quedaba
    guardada sin despachar. La calificación usa la fábrica real."""
    import src.plugins.chats.agent.sales.composition as comp
    import src.sdk.connectorkit as ck

    for name in ("get_promotions_port", "get_promo_quota_store", "get_coupon_sales_reader",
                 "get_catalog_client", "get_web_cart_reader", "get_coupon_grants_store"):
        monkeypatch.setattr(ck, name, lambda: None)
    fake_client = object()

    async def _client():
        return fake_client

    dispatch = _Dispatch()
    clients: list[Any] = []

    async def _record(envelope, client):
        clients.append(client)
        return await dispatch.record(envelope, client)

    monkeypatch.setattr(comp, "get_temporal_client", _client)
    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", _record)
    monkeypatch.setattr(comp, "_INGEST_USE_CASE", None)
    use_case = comp.build_ingest_use_case()
    metadata = _Metadata()
    monkeypatch.setattr(use_case, "_metadata_store", metadata)

    await use_case.emit_review_submitted(_SID, "wamid.REVIEW9", {"rating": "5"}, _NOW_MS)

    assert clients == [fake_client]
    assert [e.payload["wa_message_id"] for e in dispatch.envelopes] == ["wamid.REVIEW9"]
