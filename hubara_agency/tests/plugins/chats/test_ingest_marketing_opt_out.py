"""El ingest honra el opt-out prometido por el template de campañas.

Cliente con campaña reciente responde "NO MÁS" → `marketing_opt_out=true`
en su metadata → la audiencia de campañas lo excluye para siempre. Sin
esto, la promesa del copy aprobado por Meta es mentira operativa (riesgo
de report/sanción).
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)


class FakeHistoryStore:
    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        pass


class FakeLoadOrStart:
    async def execute(self, *a: Any, **kw: Any) -> None:
        pass


class FakeMetadataStore:
    def __init__(self, seed: dict[str, dict] | None = None) -> None:
        self.store: dict[str, dict] = dict(seed or {})

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)


def _msg(text: str) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.X",
        from_number="5491111111111",
        phone_number_id="PID",
        text=text,
        media=None,
        timestamp="1714312345",
    )


def _store_with_recent_touch() -> FakeMetadataStore:
    now_ms = int(time.time() * 1000)
    return FakeMetadataStore(
        {
            "wa_5491111111111": {
                "campaign_touches": [
                    {
                        "campaign_id": "mkt-1",
                        "campaign_name": "Promo",
                        "sent_at_ms": now_ms - 3_600_000,
                    }
                ]
            }
        }
    )


@pytest.mark.asyncio
async def test_no_mas_con_campana_reciente_marca_opt_out(_isolate_vault_dir):
    metadata_store = _store_with_recent_touch()
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=metadata_store,  # type: ignore[arg-type]
    )
    await use_case.execute(_msg("NO MÁS"))

    saved = metadata_store.store["wa_5491111111111"]
    assert saved["marketing_opt_out"] is True
    assert isinstance(saved["marketing_opt_out_at_ms"], int)


@pytest.mark.asyncio
async def test_texto_normal_no_marca_opt_out(_isolate_vault_dir):
    metadata_store = _store_with_recent_touch()
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=metadata_store,  # type: ignore[arg-type]
    )
    await use_case.execute(_msg("quiero 2 velas del catálogo"))

    assert "marketing_opt_out" not in metadata_store.store["wa_5491111111111"]


@pytest.mark.asyncio
async def test_no_mas_sin_campana_no_marca(_isolate_vault_dir):
    metadata_store = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=metadata_store,  # type: ignore[arg-type]
    )
    await use_case.execute(_msg("no más"))

    assert "marketing_opt_out" not in metadata_store.store["wa_5491111111111"]
