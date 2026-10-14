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


class _Dispatch:
    def __init__(self) -> None:
        self.envelopes: list[Any] = []

    async def record(self, envelope: Any, client: Any) -> None:
        self.envelopes.append(envelope)


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

    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", flaky)
    metadata = _Metadata()
    metadata.store[_SID] = {"active_route": "humano", "episodes": []}
    use_case = IngestInboundMessage(
        history_store=_History(), load_session=_LoadOrStart(), metadata_store=metadata,  # type: ignore[arg-type]
        temporal_client_factory=_factory,  # type: ignore[arg-type]
    )

    await use_case.execute(_review_reply())

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

    [pending] = metadata.read(_SID)["pending_review_submissions"]
    assert pending["wa_message_id"] == "wamid.REVIEW1"
    assert '"rating": "4"' in pending["response_json"]
