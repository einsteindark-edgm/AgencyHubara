"""El ingest de inbounds mantiene el índice de reactivación (Punto 2, escala).

En el MISMO momento en que estampa las ventanas en metadata, el ingest
actualiza la entrada liviana del índice — así el snapshot builder del Window
Strategist shortlistea sin escanear el vault entero. Best-effort: un índice
roto JAMÁS tumba el ingest (el fallback del builder es full scan).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.sdk.messagingkit import load_reengagement_index


class FakeHistoryStore:
    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        pass


class FakeLoadOrStart:
    async def execute(self, *a: Any, **kw: Any) -> None:
        pass


class FakeMetadataStore:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)


def _msg() -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.X",
        from_number="5491111111111",
        phone_number_id="PID",
        text="hola",
        media=None,
        timestamp="1714312345",
    )


@pytest.mark.asyncio
async def test_ingest_escribe_la_entrada_del_indice(_isolate_vault_dir: Path):
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
    )
    await use_case.execute(_msg())

    index = load_reengagement_index(_isolate_vault_dir)
    assert index is not None, "el ingest debe crear/actualizar el índice"
    entry = index.get("wa_5491111111111")
    assert entry is not None, index
    assert isinstance(entry["last_inbound_at_ms"], int)
    assert isinstance(entry["service_window_expires_at_ms"], int)
    assert isinstance(entry["updated_at_ms"], int)
