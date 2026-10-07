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


# --- revisión del PR #393 ------------------------------------------------------


class _WhileWaitingForJev:
    """Proveedor de lecturas que, mientras «espera a Jev», deja que otro
    escritor haga lo suyo (con el candado)."""

    def __init__(self, store: FilesystemMetadataStore, mutator: Any) -> None:
        self._store = store
        self._mutator = mutator

    async def read(self, inbound: Inbound) -> Readings:
        self._store.update(inbound.session_id, self._mutator)
        return Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=False)


def _ingest(store: FilesystemMetadataStore, mutator: Any) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=_Loader(),  # type: ignore[arg-type]
        metadata_store=store,
        readings=_WhileWaitingForJev(store, mutator),
    )


@pytest.mark.asyncio
async def test_two_writes_in_one_execute_keep_the_history_entry_of_another_writer(
    _isolate_vault_dir, monkeypatch
) -> None:
    """D2: el cliente vuelve tras agotar la escalera (escritura 1 del ingest:
    `ingest:customer_returned`) y manda un PDF (escritura 2: la ruta humana).
    Mientras el ingest esperaba a Jev, otro escritor agregó su entrada al
    historial: tiene que sobrevivir a las DOS escrituras."""
    from unittest.mock import AsyncMock

    import src.platform.whatsapp.client as wa_client

    monkeypatch.setattr(
        "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
        AsyncMock(return_value=(b"%PDF-1.4 comprobante", "application/pdf")),
    )
    monkeypatch.setattr(wa_client, "send_message", AsyncMock(return_value=None))
    store = FilesystemMetadataStore(_isolate_vault_dir)
    now_ms = int(time.time() * 1000)
    store.write(
        SID,
        {
            "phone_number_id": "pnid-1",
            "active_route": "ventas",
            "tag": "SIN_RESPUESTA",
            "status_history": [{"tag": "SIN_RESPUESTA", "timestamp": 1.0}],
            "episodes": [{"episode_id": "ep_001", "started_at_ms": now_ms - 60_000, "closed_at_ms": None}],
        },
    )

    def other_writer_appends(fresh: dict[str, Any]) -> dict[str, Any]:
        fresh["status_history"] = [
            *fresh["status_history"],
            {"tag": "INTERESADO", "timestamp": 2.0, "source": "otro_escritor"},
        ]
        return fresh

    message = WhatsAppMessage(
        message_id="wamid.doc",
        from_number="573001234567",
        phone_number_id="pnid-1",
        text=None,
        media={"type": "document", "id": "doc-1", "mime_type": "application/pdf", "filename": "comprobante.pdf"},
        timestamp=str(int(time.time())),
    )

    await _ingest(store, other_writer_appends).execute(message)

    sources = [e.get("source") or e["tag"] for e in store.read(SID)["status_history"]]
    assert "otro_escritor" in sources, sources
    assert sources[-2:] == ["ingest:customer_returned", "HUMANO"], sources


@pytest.mark.asyncio
async def test_a_human_who_took_over_during_the_wait_keeps_the_conversation(_isolate_vault_dir) -> None:
    """M3: el mensaje reabría un episodio (el anterior cerró con compra), pero
    mientras el ingest esperaba a Jev el operador tomó la conversación. Ni
    `tag=NO_ETIQUETADO` ni un episodio nuevo abierto: manda el humano."""
    store = FilesystemMetadataStore(_isolate_vault_dir)
    now_ms = int(time.time() * 1000)
    closed = {
        "episode_id": "ep_001",
        "started_at_ms": now_ms - 3 * 86_400_000,
        "closed_at_ms": now_ms - 2 * 86_400_000,
        "closing_tag": "COMPRA_EXITOSA",
    }
    store.write(
        SID,
        {"phone_number_id": "pnid-1", "active_route": "ventas", "tag": "COMPRA_EXITOSA", "episodes": [closed]},
    )

    def operator_takes_over(fresh: dict[str, Any]) -> dict[str, Any]:
        return {**fresh, "active_route": "humano", "tag": "HUMANO", "motivo": "Humano tomó el control"}

    message = WhatsAppMessage(
        message_id="wamid.in",
        from_number="573001234567",
        phone_number_id="pnid-1",
        text="hola, una pregunta",
        media=None,
        timestamp=str(int(time.time())),
    )

    await _ingest(store, operator_takes_over).execute(message)

    metadata = store.read(SID)
    assert (metadata["active_route"], metadata["tag"]) == ("humano", "HUMANO")
    assert metadata["motivo"] == "Humano tomó el control"
    assert metadata["episodes"] == [closed], "quedó un episodio nuevo abierto bajo el humano"
    # Lo demás del mensaje sí quedó escrito.
    assert metadata["last_inbound_message_id"] == "wamid.in"


class _TakeoverRightBeforeTheWrite(FilesystemMetadataStore):
    """El operador toma la conversación justo cuando el ingest va a escribir
    la rotación: después de cualquier chequeo previo y antes del candado."""

    def __init__(self, vault: Any) -> None:
        super().__init__(vault)
        self.taken = False

    def write_merged(self, session_id: str, *, base: dict[str, Any], ours: dict[str, Any], **kw: Any) -> Any:
        rotates = ours.get("tag") == "NO_ETIQUETADO" and base.get("tag") != "NO_ETIQUETADO"
        if rotates and not self.taken:
            self.taken = True
            self.update(
                session_id,
                lambda fresh: {**fresh, "active_route": "humano", "tag": "HUMANO", "motivo": "Humano tomó el control"},
            )
        return super().write_merged(session_id, base=base, ours=ours, **kw)


@pytest.mark.asyncio
async def test_the_human_takeover_is_decided_under_the_lock(_isolate_vault_dir) -> None:
    """Segunda revisión del PR #393 (M3): «¿tomó un humano?» se decidía con
    una lectura ANTES del candado; una toma entre esa lectura y la escritura
    dejaba otra vez `tag=NO_ETIQUETADO` y un episodio abierto bajo el humano.
    Se decide con lo que hay en disco bajo el candado de la escritura."""
    store = _TakeoverRightBeforeTheWrite(_isolate_vault_dir)
    now_ms = int(time.time() * 1000)
    closed = {
        "episode_id": "ep_001",
        "started_at_ms": now_ms - 3 * 86_400_000,
        "closed_at_ms": now_ms - 2 * 86_400_000,
        "closing_tag": "COMPRA_EXITOSA",
    }
    store.write(
        SID,
        {"phone_number_id": "pnid-1", "active_route": "ventas", "tag": "COMPRA_EXITOSA", "episodes": [closed]},
    )
    message = WhatsAppMessage(
        message_id="wamid.in",
        from_number="573001234567",
        phone_number_id="pnid-1",
        text="hola, una pregunta",
        media=None,
        timestamp=str(int(time.time())),
    )

    await _ingest(store, lambda fresh: None).execute(message)

    assert store.taken, "la escritura de la rotación no pasó por write_merged"
    metadata = store.read(SID)
    assert (metadata["active_route"], metadata["tag"]) == ("humano", "HUMANO")
    assert metadata["episodes"] == [closed], "quedó un episodio nuevo abierto bajo el humano"
