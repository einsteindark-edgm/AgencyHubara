"""Ingest: levantar `SIN_RESPUESTA` y re-abrir la ventana CTWA de 72h.

1. Decisión 2026-09-18 (escalera de reactivación): `SIN_RESPUESTA` marca a
   quien agotó los 5 toques sin contestar, para filtrarlo después. Si el
   cliente VUELVE a escribir deja de ser "sin respuesta": el ingest levanta la
   etiqueta (determinista, sin LLM) y deja rastro en `status_history`.
2. La ventana gratis de 72h se estampaba UNA vez para siempre
   (`"ctwa_window_expires_at_ms" not in metadata`): un cliente que volvía
   semanas después por OTRO anuncio no abría ventana nueva aunque Meta sí se
   la da (Free Entry Point por cada entrada desde anuncio).
"""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from tests.test_ingest_inbound_message import (
    FakeHistoryStore,
    FakeLoadOrStart,
    FakeMetadataStore,
    _make_text_message,
)

SID = "wa_5491111111111"
H = 60 * 60 * 1000


def _use_case(metadata: FakeMetadataStore) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )


def _ad_message(clid: str) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=f"wamid.{clid}",
        from_number="5491111111111",
        phone_number_id="PID",
        text="Hola, vi el anuncio",
        media=None,
        timestamp="1714312400",
        msg_type="text",
        referral={"ctwa_clid": clid, "source_type": "ad", "source_id": "AD_1",
                  "headline": "Velas Hubara", "body": "x"},
    )


@pytest.mark.asyncio
async def test_inbound_levanta_la_etiqueta_sin_respuesta():
    metadata = FakeMetadataStore()
    metadata.store[SID] = {
        "tag": "SIN_RESPUESTA",
        "motivo": "Escalera de reactivación agotada",
        "active_route": "ventas",
        "episodes": [{"episode_id": "ep_001", "closed_at_ms": None}],
    }
    await _use_case(metadata).execute(_make_text_message(text="hola, sigo interesada"))

    state = metadata.store[SID]
    assert state["tag"] == "NO_ETIQUETADO"
    assert state["status_history"][-1]["source"] == "ingest:customer_returned"


@pytest.mark.asyncio
async def test_inbound_no_toca_otros_tags():
    metadata = FakeMetadataStore()
    metadata.store[SID] = {
        "tag": "INTERESADO",
        "episodes": [{"episode_id": "ep_001", "closed_at_ms": None}],
    }
    await _use_case(metadata).execute(_make_text_message(text="hola"))
    assert metadata.store[SID]["tag"] == "INTERESADO"


@pytest.mark.asyncio
async def test_anuncio_nuevo_con_ventana_vencida_abre_otra_de_72h():
    metadata = FakeMetadataStore()
    metadata.store[SID] = {"ctwa_window_expires_at_ms": 1_000}  # venció hace años
    await _use_case(metadata).execute(_ad_message("CLID_NUEVO"))

    state = metadata.store[SID]
    assert state["ctwa_window_expires_at_ms"] - state["last_inbound_at_ms"] == 72 * H


@pytest.mark.asyncio
async def test_anuncio_con_ventana_abierta_no_la_extiende():
    # Meta NO renueva la ventana gratis mientras sigue abierta.
    metadata = FakeMetadataStore()
    far_future = 9_999_999_999_999
    metadata.store[SID] = {"ctwa_window_expires_at_ms": far_future}
    await _use_case(metadata).execute(_ad_message("CLID_OTRO"))
    assert metadata.store[SID]["ctwa_window_expires_at_ms"] == far_future
