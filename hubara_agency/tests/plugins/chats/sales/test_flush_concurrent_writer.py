"""Incidente 2026-10-06 (conversación de prueba): una tarjeta ya entregada
volvió a la cola y salió dos veces.

Turno 4: el bot llamó `present_product_detail`; el flush mandó la foto, la sacó
de `pending_ui_intents` y anotó su entrega en `outbound_media_index`. Turno 5:
el bot NO pidió la foto (solo `get_product_by_handle` + `present_variant_picker`)
y aun así el flush mandó otra vez la foto y después el selector; en el índice
de fotos faltaba la entrega del turno 4. Alguien leyó `metadata.json` con la
foto todavía en cola y escribió DESPUÉS de que el flush la sacó.

Mecanismo reproducido acá: el aviso de entrega de WhatsApp
(`IngestDeliveryStatus`) hace `update()` — lee bajo el candado y escribe — y
el flush escribía su copia SIN candado: su escritura cayó entre la lectura y
la escritura del aviso, que la pisó con lo que leyó antes.

Contrato:
  * la foto entregada no vuelve a `pending_ui_intents` y su entrada del índice
    se conserva, aunque otro escritor esté a mitad de un `update()`;
  * un intent cuyo id ya figura como entregado no se manda otra vez aunque una
    escritura vieja lo devuelva a la cola (también los encolados sin `id`).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from temporalio.testing import ActivityEnvironment

SESSION = "wa_573001234567"
PHOTO_WAMID = "wamid.foto.t4"


def _photo_intent(intent_id: str | None = "foto-t4") -> dict[str, Any]:
    intent: dict[str, Any] = {
        "kind": "product_detail",
        "queued_at_ms": int(time.time() * 1000),
        "analytics": {"component_id": "product_detail", "component_kind": "image"},
        "params": {
            "handle": "cubo-love",
            "title": "Cubo Love",
            "image_url": "https://assets.example.com/cubo-love.webp",
            "caption": "Cubo Love · $35.000 COP",
            "design": "Rosa",
        },
    }
    if intent_id is not None:
        intent["id"] = intent_id
    return intent


def _seed(vault: Path, intents: list[dict[str, Any]]) -> Path:
    path = vault / SESSION / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "phone_number_id": "pnid-1",
                "episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}],
                "pending_ui_intents": intents,
                "outbound_media_index": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from src.platform import config

    monkeypatch.setattr(config, "WORKSPACE_VAULT_DIR", tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_photo_sent_while_a_delivery_status_update_is_in_flight_stays_delivered(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.platform.state import FilesystemMetadataStore
    from src.platform.whatsapp import client as wa_client
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
        flush_pending_ui_intents_activity,
    )

    path = _seed(vault, [_photo_intent()])
    other_writer_read = threading.Event()
    other_writers: list[threading.Thread] = []

    def delivery_status_mutator(fresh: dict[str, Any]) -> dict[str, Any]:
        # Lee con la foto todavía en cola y tarda en escribir (el aviso de
        # entrega materializando el precio): sostiene el candado medio segundo.
        other_writer_read.set()
        time.sleep(0.5)
        fresh["last_delivery_status"] = {"wa_message_id": "wamid.texto.t4", "status": "delivered"}
        return fresh

    async def send_image(*args: Any, **kwargs: Any) -> SimpleNamespace:
        writer = threading.Thread(
            target=FilesystemMetadataStore(vault).update,
            args=(SESSION, delivery_status_mutator),
            daemon=True,
        )
        writer.start()
        other_writers.append(writer)
        assert await asyncio.to_thread(other_writer_read.wait, 5)
        return SimpleNamespace(ok=True, wa_message_id=PHOTO_WAMID, error=None)

    send_image_mock = AsyncMock(side_effect=send_image)
    monkeypatch.setattr(wa_client, "send_image", send_image_mock)

    report = await ActivityEnvironment().run(flush_pending_ui_intents_activity, SESSION)
    for writer in other_writers:
        writer.join(5)

    assert report == [{"kind": "product_detail", "wamid": PHOTO_WAMID, "ok": True}]
    metadata = _read(path)
    assert metadata["pending_ui_intents"] == [], "la foto entregada volvió a la cola"
    assert PHOTO_WAMID in metadata["outbound_media_index"], "se perdió la entrega del índice de fotos"
    assert metadata["last_delivery_status"]["status"] == "delivered", "se perdió el aviso de entrega"

    # El turno siguiente no la manda otra vez.
    await ActivityEnvironment().run(flush_pending_ui_intents_activity, SESSION)
    assert send_image_mock.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("intent_id", ["foto-t4", None], ids=["con-id", "encolado-sin-id"])
async def test_intent_already_delivered_is_not_sent_again(
    vault: Path, monkeypatch: pytest.MonkeyPatch, intent_id: str | None
) -> None:
    """Una escritura vieja devuelve a la cola la foto ya entregada (lo que pasó
    entre el turno 4 y el 5): el flush la descarta sin mandarla."""
    from src.platform.state import FilesystemMetadataStore
    from src.platform.whatsapp import client as wa_client
    from src.plugins.chats.agent.sales.activities import flush_ui_intents

    photo = _photo_intent(intent_id)
    path = _seed(vault, [photo])
    send_image = AsyncMock(return_value=SimpleNamespace(ok=True, wa_message_id=PHOTO_WAMID, error=None))
    monkeypatch.setattr(wa_client, "send_image", send_image)

    assert await flush_ui_intents.flush_pending_ui_intents(SESSION) == 1

    def stale_copy_brings_it_back(fresh: dict[str, Any]) -> dict[str, Any]:
        fresh["pending_ui_intents"] = [photo]
        return fresh

    FilesystemMetadataStore(vault).update(SESSION, stale_copy_brings_it_back)

    assert await flush_ui_intents.flush_pending_ui_intents(SESSION) == 0
    assert send_image.await_count == 1, "la foto ya entregada salió otra vez"
    assert _read(path)["pending_ui_intents"] == []


@pytest.mark.asyncio
async def test_a_new_intent_queued_during_the_flush_is_kept(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lo que una tool encola mientras el flush envía no se pierde: el flush
    saca SOLO lo que mandó."""
    from src.platform.state import FilesystemMetadataStore
    from src.platform.whatsapp import client as wa_client
    from src.plugins.chats.agent.sales.activities import flush_ui_intents

    path = _seed(vault, [_photo_intent()])
    picker = {"id": "picker-t5", "kind": "variant_picker", "queued_at_ms": int(time.time() * 1000), "params": {}}

    async def send_image(*args: Any, **kwargs: Any) -> SimpleNamespace:
        FilesystemMetadataStore(vault).update(
            SESSION, lambda fresh: {**fresh, "pending_ui_intents": [*fresh["pending_ui_intents"], picker]}
        )
        return SimpleNamespace(ok=True, wa_message_id=PHOTO_WAMID, error=None)

    monkeypatch.setattr(wa_client, "send_image", AsyncMock(side_effect=send_image))

    assert await flush_ui_intents.flush_pending_ui_intents(SESSION) == 1
    assert _read(path)["pending_ui_intents"] == [picker]


@pytest.mark.asyncio
async def test_a_failed_send_does_not_block_a_legit_requeue_with_the_same_id(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revisión del PR #393 (M1): las instrucciones de pago llevan un id fijo
    por pedido (`payinstr-<order_id>`). Si el primer envío falla, un reencolado
    legítimo (mismo id, otro `queued_at_ms`) sale; el MISMO intent fallido
    devuelto por una escritura vieja, no."""
    from src.platform.state import FilesystemMetadataStore
    from src.platform.whatsapp import client as wa_client
    from src.plugins.chats.agent.sales.activities import flush_ui_intents

    first = _photo_intent("payinstr-order_1")
    path = _seed(vault, [first])
    send_image = AsyncMock(
        side_effect=[
            SimpleNamespace(ok=False, wa_message_id=None, error="http_500"),
            SimpleNamespace(ok=True, wa_message_id=PHOTO_WAMID, error=None),
        ]
    )
    monkeypatch.setattr(wa_client, "send_image", send_image)

    assert await flush_ui_intents.flush_pending_ui_intents(SESSION) == 0
    store = FilesystemMetadataStore(vault)

    # Una escritura vieja devuelve el MISMO intent fallido: no se reintenta solo.
    store.update(SESSION, lambda fresh: {**fresh, "pending_ui_intents": [first]})
    assert await flush_ui_intents.flush_pending_ui_intents(SESSION) == 0
    assert send_image.await_count == 1

    # Reencolado legítimo: mismo id, otro momento.
    requeued = {**first, "queued_at_ms": first["queued_at_ms"] + 1_000}
    store.update(SESSION, lambda fresh: {**fresh, "pending_ui_intents": [requeued]})
    assert await flush_ui_intents.flush_pending_ui_intents(SESSION) == 1
    assert send_image.await_count == 2
    assert _read(path)["pending_ui_intents"] == []


def test_the_delivery_log_is_read_bounded_to_its_tail(tmp_path: Path) -> None:
    """M7: el registro crece con la sesión; se leen solo los últimos 64 KiB
    (un intent vence a los 10 min) y la primera línea, cortada, se ignora."""
    from src.plugins.chats.agent.sales.activities import flush_ui_intents

    session_dir = tmp_path / SESSION
    session_dir.mkdir()

    def row(intent_id: str) -> str:
        return json.dumps({"id": intent_id, "kind": "product_detail", "ok": True, "queued_at_ms": 1}) + "\n"

    filler = "".join(row(f"relleno-{n:05d}") for n in range(1_500))
    (session_dir / "ui_intents_delivered.jsonl").write_text(row("viejo") + filler + row("reciente"), encoding="utf-8")
    assert (session_dir / "ui_intents_delivered.jsonl").stat().st_size > 64 * 1024

    log = flush_ui_intents._delivery_log(session_dir)

    assert "reciente" in log.delivered
    assert "viejo" not in log.delivered, "se leyó el registro entero"
    assert all(i == "reciente" or i.startswith("relleno-") for i in log.delivered)
