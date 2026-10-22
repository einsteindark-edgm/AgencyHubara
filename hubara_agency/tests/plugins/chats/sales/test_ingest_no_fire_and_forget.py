"""El ingest no deja tareas sueltas (entrada durable, fase 2; lección L-7).

Hasta la fase 1 los eventos de analytics, el aviso al watchdog, la visión y el
audio salían con `_spawn_safe` (`asyncio.create_task` sin guardar la
referencia): el GC puede matar la tarea sin log y, dentro de una activity,
sobrevivía al final del intento — si el worker moría, se perdía. Ahora cada
efecto se espera (con tope) dentro del paso que lo produce, y si falla se
loguea sin tumbar el mensaje.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

import src.plugins.chats.agent.sales.use_cases.ingest_inbound_message as ingest_mod
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from tests.metadata_store_fakes import MergingMetadataStoreMixin

_SID = "wa_573001234567"
_NOW_MS = 1_791_000_000_000


class _History:
    def append_user_event(self, session_id: str, content: str, **_: Any) -> None:
        pass


class _Bot:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, session_id: str, message: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(message)


class _Metadata(MergingMetadataStoreMixin):
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)


class _Bus:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.kinds: list[str] = []

    async def record(self, event: Any) -> None:
        if self.fail:
            raise RuntimeError("sink roto")
        self.kinds.append(getattr(event, "event_type", type(event).__name__))


async def _factory() -> object:
    return object()


def _button(text: str = "Sí, la quiero") -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.BTN",
        from_number=_SID.removeprefix("wa_"),
        phone_number_id="PID",
        text=None,
        media=None,
        timestamp=str(_NOW_MS // 1000),
        msg_type="interactive",
        interactive={"type": "button_reply", "id": "si", "title": text},  # como lo deja el parser
    )


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW_MS)
    seen: list[str] = []

    async def record(envelope: Any, client: Any) -> None:
        seen.append(envelope.event_type)

    monkeypatch.setattr(ingest_mod, "dispatch_envelope_with_client", record)
    return seen


def _ingest(bot: _Bot, bus: _Bus | None = None) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=bot,  # type: ignore[arg-type]
        metadata_store=_Metadata(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        temporal_client_factory=_factory,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_the_watchdog_is_told_before_the_ingest_returns(dispatched) -> None:
    await _ingest(_Bot()).execute(_button())

    assert dispatched == ["ServiceWindowOpenedEvent"]  # sin ceder el loop: ya salió


@pytest.mark.asyncio
async def test_the_click_is_recorded_before_the_ingest_returns(dispatched) -> None:
    bus = _Bus()

    await _ingest(_Bot(), bus).execute(_button())

    assert bus.kinds  # el evento del clic ya está en el sink al volver


@pytest.mark.asyncio
async def test_a_broken_analytics_sink_does_not_lose_the_message(dispatched) -> None:
    bot = _Bot()

    await _ingest(bot, _Bus(fail=True)).execute(_button())

    assert bot.calls == ["[el cliente tocó el botón: Sí, la quiero]"]


def test_the_ingest_module_spawns_no_loose_tasks() -> None:
    """L-7: nada de `create_task` ni `_spawn_safe` en el ingest."""
    source = Path(ingest_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute | ast.Name)
    }
    assert "create_task" not in names
    assert "_spawn_safe" not in names
