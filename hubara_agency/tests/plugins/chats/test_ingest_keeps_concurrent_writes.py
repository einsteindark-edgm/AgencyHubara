"""El ingest no revive lo que otro escritor cambió mientras esperaba a Jev.

Incidente 2026-10-06 (conversación de prueba): el ingest del mensaje del
turno 5 leyó `metadata.json` con la foto del turno 4 todavía en cola, esperó
varios segundos las lecturas del motor (Jev) y escribió su copia ENTERA. En
esa espera el flush del turno 4 ya había mandado la foto, la había sacado de
la cola y había anotado su entrega en `outbound_media_index`: la copia vieja
devolvió la foto a la cola (salió otra vez) y borró la entrega del índice.

Contrato: el ingest escribe SOLO lo que él cambió (merge de tres vías contra
lo que leyó); lo demás queda como está en disco.
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.decisions.readings import Inbound, Readings
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage

SID = "wa_573001234567"
PHOTO_WAMID = "wamid.foto.t4"


class _History:
    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        pass

    def read_events(self, session_id: str) -> list[dict]:
        return []


class _Loader:
    async def execute(self, *a: Any, **kw: Any) -> None:
        pass


class _FlushWhileWaitingForJev:
    """Proveedor de lecturas lento: mientras «espera a Jev», el flush del turno
    anterior saca la foto de la cola y anota su entrega (con el candado)."""

    def __init__(self, store: FilesystemMetadataStore) -> None:
        self._store = store

    async def read(self, inbound: Inbound) -> Readings:
        def flush_pops_the_photo(fresh: dict[str, Any]) -> dict[str, Any]:
            fresh["pending_ui_intents"] = []
            fresh.setdefault("outbound_media_index", {})[PHOTO_WAMID] = {
                "handle": "cubo-love",
                "title": "Cubo Love",
                "image_url": "https://assets.example.com/cubo-love.webp",
                "label": "Rosa",
            }
            return fresh

        self._store.update(inbound.session_id, flush_pops_the_photo)
        return Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=False)


def _photo_intent() -> dict[str, Any]:
    return {
        "id": "foto-t4",
        "kind": "product_detail",
        "queued_at_ms": int(time.time() * 1000),
        "params": {"handle": "cubo-love", "image_url": "https://assets.example.com/cubo-love.webp"},
    }


@pytest.mark.asyncio
async def test_ingest_does_not_bring_back_the_photo_the_flush_already_sent(_isolate_vault_dir) -> None:
    store = FilesystemMetadataStore(_isolate_vault_dir)
    now_ms = int(time.time() * 1000)
    store.write(
        SID,
        {
            "phone_number_id": "pnid-1",
            "active_route": "ventas",
            "episodes": [{"episode_id": "ep_001", "started_at_ms": now_ms - 60_000, "closed_at_ms": None}],
            "pending_ui_intents": [_photo_intent()],
            "outbound_media_index": {},
        },
    )
    ingest = IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=_Loader(),  # type: ignore[arg-type]
        metadata_store=store,
        readings=_FlushWhileWaitingForJev(store),
    )
    message = WhatsAppMessage(
        message_id="wamid.in.t5",
        from_number="573001234567",
        phone_number_id="pnid-1",
        text="¿y la tienes en lila?",
        media=None,
        timestamp=str(int(time.time())),
    )

    await ingest.execute(message)

    metadata = store.read(SID)
    assert metadata["pending_ui_intents"] == [], "el ingest devolvió a la cola la foto ya entregada"
    assert PHOTO_WAMID in metadata["outbound_media_index"], "el ingest borró la entrega del índice de fotos"
    # …y lo suyo sí quedó escrito.
    assert metadata["last_inbound_message_id"] == "wamid.in.t5"
    assert isinstance(metadata["last_inbound_at_ms"], int)
