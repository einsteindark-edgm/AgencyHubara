"""La ventana de servicio de 24 h se abre cuando el CLIENTE escribió, no cuando
llegó el webhook.

Meta reintenta un webhook que no aceptamos hasta ~7 días (caso 2026-10-09:
clientes con nombre de usuario rechazados por el parser; tras el fix, Meta los
re-entrega días después). Con la ventana contada desde la llegada, el sistema
la creía abierta: el bot contestaba texto libre, Meta lo rechazaba (131047) y
el panel no le ofrecía al operador «Reactivar conversación» con plantilla.

Contrato:
* `last_inbound_at_ms` / `service_window_expires_at_ms` salen de
  `min(llegada, messages[].timestamp)` (un reloj de Meta adelantado no estira
  la ventana);
* solo se confía en el timestamp dentro del horizonte de reintentos de Meta
  (7 días + 1 de margen): uno más viejo no puede ser una re-entrega real, es
  un reloj sintético (simulador, laboratorio) y cuenta la llegada;
* un mensaje que llega con su ventana YA cerrada queda en el chat, pero no
  dispara el turno del bot ni el watchdog — no se pasa al humano (una falla
  técnica no es un traspaso): el operador lo ve con la ventana cerrada.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

import src.plugins.chats.agent.sales.use_cases.ingest_inbound_message as ingest_mod
from src.platform.whatsapp.window import SERVICE_WINDOW_MS
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from tests.metadata_store_fakes import MergingMetadataStoreMixin

_SID = "wa_573001234567"
_NOW_MS = 1_791_000_000_000  # 2026-10-03


class _History:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def append_user_event(self, session_id: str, content: str, **_: Any) -> None:
        self.events.append((session_id, content))


class _LoadOrStart:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def execute(self, session_id: str, message: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((session_id, message))


class _Metadata(MergingMetadataStoreMixin):
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)


class _Dispatch:
    def __init__(self) -> None:
        self.event_types: list[str] = []

    async def record(self, envelope: Any, client: Any) -> None:
        self.event_types.append(envelope.event_type)


async def _factory() -> object:
    return object()


def _message(sent_ms: int) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.LATE",
        from_number=_SID.removeprefix("wa_"),
        phone_number_id="PID",
        text="hola, ¿tienen velas de halloween?",
        media=None,
        timestamp=str(sent_ms // 1000),
    )


@pytest.fixture
def ingest(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW_MS)
    dispatch = _Dispatch()
    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", dispatch.record)
    history, bot, metadata = _History(), _LoadOrStart(), _Metadata()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=bot,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
        temporal_client_factory=_factory,  # type: ignore[arg-type]
    )

    async def run(sent_ms: int) -> dict[str, Any]:
        await use_case.execute(_message(sent_ms))
        for _ in range(6):
            await asyncio.sleep(0)
        return metadata.read(_SID)

    return run, history, bot, dispatch


@pytest.mark.asyncio
async def test_a_message_redelivered_days_later_keeps_its_window_closed(ingest) -> None:
    run, history, bot, dispatch = ingest
    sent_ms = _NOW_MS - 3 * 24 * 3600 * 1000

    meta = await run(sent_ms)

    assert meta["last_inbound_at_ms"] == sent_ms
    assert meta["service_window_expires_at_ms"] == sent_ms + SERVICE_WINDOW_MS  # ya cerrada
    assert history.events == [(_SID, "hola, ¿tienen velas de halloween?")]  # el operador lo ve
    assert bot.calls == []  # el bot no le escribe texto libre fuera de ventana
    assert dispatch.event_types == []  # ni se programa un watchdog sobre una ventana cerrada
    assert meta.get("active_route") != "humano"  # no es un traspaso


@pytest.mark.asyncio
async def test_a_message_that_arrives_on_time_opens_the_window_from_when_it_was_sent(ingest) -> None:
    run, _history, bot, dispatch = ingest
    sent_ms = _NOW_MS - 40_000  # Meta demoró 40 s

    meta = await run(sent_ms)

    assert meta["service_window_expires_at_ms"] == sent_ms + SERVICE_WINDOW_MS
    assert bot.calls == [(_SID, "hola, ¿tienen velas de halloween?")]
    assert dispatch.event_types == ["ServiceWindowOpenedEvent"]


@pytest.mark.asyncio
async def test_a_meta_clock_ahead_of_ours_does_not_stretch_the_window(ingest) -> None:
    run, _history, bot, _dispatch = ingest

    meta = await run(_NOW_MS + 120_000)

    assert meta["service_window_expires_at_ms"] == _NOW_MS + SERVICE_WINDOW_MS
    assert bot.calls != []


@pytest.mark.asyncio
async def test_a_timestamp_older_than_meta_retries_is_not_a_redelivery(ingest) -> None:
    run, _history, bot, _dispatch = ingest

    meta = await run(_NOW_MS - 30 * 24 * 3600 * 1000)

    assert meta["service_window_expires_at_ms"] == _NOW_MS + SERVICE_WINDOW_MS
    assert bot.calls != []


def test_the_fixture_clock_is_in_the_past() -> None:
    """Guarda del propio test: `_NOW_MS` fijo, no el reloj real."""
    assert _NOW_MS < time.time() * 1000
