"""El operador manda un texto desde el panel: anotar el envío que YA SALIÓ no
puede devolverle un error ni dejar el mensaje fuera del historial (PR #393,
octava revisión).

`send_human_message` manda a WhatsApp y DESPUÉS marca el `client_message_id`
como enviado (`_mark_sent`) y anota el historial. Si la marca lanzaba (un
EIO), el operador recibía un 500 por un mensaje que el cliente sí recibió y el
historial no lo mostraba. Misma regla que el flush: el envío ya ocurrió.
"""
from __future__ import annotations

import errno
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog

import src.platform.whatsapp.activities as wa
import src.plugins.chats.api.handoff as handoff
from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.dtos import OutboundResult

SID = "wa_573001234567"


class _History:
    def __init__(self) -> None:
        self.events: list[str] = []

    def append_human_event(self, session_id: str, content: str, **kw: Any) -> None:
        self.events.append(content)


class _MarkingFails:
    """El store de verdad; la marca posterior al envío (`_mark_sent`) falla:
    `once=True`, con un EIO de un instante dentro del store (que lo reintenta);
    `once=False`, siempre."""

    def __init__(self, store: FilesystemMetadataStore, monkeypatch: pytest.MonkeyPatch, *, once: bool) -> None:
        self._store = store
        self._once = once
        self._monkeypatch = monkeypatch

    def update(self, session_id: str, mutator: Any) -> Any:
        if getattr(mutator, "__name__", "") != "_mark_sent":
            return self._store.update(session_id, mutator)
        if not self._once:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        real_read_text = Path.read_text
        left = {"n": 1}

        def read_text(path: Path, *args: Any, **kwargs: Any) -> str:
            if path.name == "metadata.json" and left["n"]:
                left["n"] -= 1
                raise OSError(errno.EIO, os.strerror(errno.EIO))
            return real_read_text(path, *args, **kwargs)

        with self._monkeypatch.context() as patched:
            patched.setattr(Path, "read_text", read_text)
            return self._store.update(session_id, mutator)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FilesystemMetadataStore:
    monkeypatch.setattr(wa, "WORKSPACE_VAULT_DIR", tmp_path, raising=True)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pnid-test")
    store = FilesystemMetadataStore(tmp_path)
    store.write(
        SID,
        {"phone_number_id": "pnid-1", "active_route": "humano", "tag": "HUMANO",
         "service_window_expires_at_ms": int(time.time() * 1000) + 3_600_000,
         "episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}]},
    )
    return store


async def _send(metadata_store: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, AsyncMock, _History]:
    send_text = AsyncMock(return_value=OutboundResult(wa_message_id="wamid.h1", ok=True))
    monkeypatch.setattr(wa.whatsapp_client, "send_text", send_text)
    monkeypatch.setattr(wa.asyncio, "sleep", AsyncMock())
    history = _History()
    response = await handoff.send_human_message(
        SID,
        handoff.SendMessageRequest(text="Hola, soy Ana del equipo", client_message_id="cmid-1"),
        metadata_store,
        history,
    )
    return response, send_text, history


@pytest.mark.asyncio
async def test_a_transient_error_marking_the_send_is_retried_and_the_operator_sees_it_sent(
    store: FilesystemMetadataStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, send_text, history = await _send(_MarkingFails(store, monkeypatch, once=True), monkeypatch)

    assert response.ok is True
    assert send_text.await_count == 1
    assert history.events == ["Hola, soy Ana del equipo"]
    data = store.read(SID)
    assert "cmid-1" in data.get("sent_human_message_ids", [])
    assert "cmid-1" not in (data.get("pending_human_sends") or {}), "el cmid quedó pendiente"


@pytest.mark.asyncio
async def test_a_mark_that_keeps_failing_after_the_send_still_answers_ok_and_keeps_the_history(
    store: FilesystemMetadataStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    with structlog.testing.capture_logs() as logs:
        response, send_text, history = await _send(_MarkingFails(store, monkeypatch, once=False), monkeypatch)

    assert response.ok is True, "un 500 por un mensaje que el cliente sí recibió"
    assert send_text.await_count == 1
    assert history.events == ["Hola, soy Ana del equipo"], "el mensaje enviado no quedó en el historial"
    assert any(e.get("log_level") == "error" for e in logs)
