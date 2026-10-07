"""El flush de UI intents no pisa lo que otro escritor guardó mientras enviaba.

Bug (2026-09-29, API real con datos sintéticos): `flush_pending_ui_intents`
leía `metadata.json` UNA vez, esperaba cada envío a WhatsApp y reescribía el
archivo ENTERO desde esa copia vieja tras cada intent. El ingest del webhook
escribe el mismo archivo con `FilesystemMetadataStore.update()` (flock +
lectura fresca + escritura atómica): un mensaje del cliente que entraba con un
envío en vuelo se borraba del metadata (`last_inbound_*` volvía atrás;
reproducido con un envío de 3 s) y un intent encolado en ese lapso desaparecía
sin enviarse. Desde la app del operador el flush corre además dentro del
proceso de la API.

Contrato: cada escritura del flush es un read-modify-write bajo el lock del
store que aplica SOLO sus propios cambios sobre el metadata FRESCO.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.activities import flush_ui_intents

_SID = "wa_test_flush_race"
_T0 = 1_790_000_000_000  # último mensaje del cliente cuando arranca el flush
_T1 = _T0 + 3_000  # el cliente escribe mientras la foto va en camino


def _now() -> int:
    return int(time.time() * 1000)


def _photo(**over: Any) -> dict[str, Any]:
    return {
        "id": "ui_photo",
        "kind": "product_detail",
        "queued_at_ms": _now(),
        "analytics": {},
        "params": {"image_url": "https://cdn.test/duo.jpg", "caption": "Dúo Zodiacal", "handle": "duo-zodiacal"},
        **over,
    }


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from src.platform import config

    monkeypatch.setattr(config, "WORKSPACE_VAULT_DIR", tmp_path)
    return tmp_path


def _seed(vault: Path, **extra: Any) -> None:
    metadata = {
        "phone_number_id": "pnid-1",
        "last_inbound_at_ms": _T0,
        # sesión de anuncio con episodio activo: la foto enviada encola ViewContent
        "ctwa_referrals": [{"ctwa_clid": "CLID_TEST", "captured_at_ms": _T0 - 1_000}],
        "episodes": [{"episode_id": "ep_001", "started_at_ms": _T0 - 60_000, "closed_at_ms": None}],
        "pending_ui_intents": [_photo()],
        **extra,
    }
    (vault / _SID).mkdir(parents=True, exist_ok=True)
    (vault / _SID / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")


def _meta(vault: Path) -> dict[str, Any]:
    return json.loads((vault / _SID / "metadata.json").read_text(encoding="utf-8"))


def _others_write_during_the_send(
    vault: Path, monkeypatch: pytest.MonkeyPatch, late: dict[str, Any], *, ok: bool = True
) -> None:
    """El envío a WhatsApp tarda; mientras tanto el ingest guarda el mensaje
    nuevo del cliente y alguien encola otro intent (ambos con `update()`)."""
    from src.platform.whatsapp import client as wa_client

    async def slow_send(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        def meanwhile(fresh: dict[str, Any]) -> dict[str, Any]:
            fresh["last_inbound_at_ms"] = _T1
            fresh.setdefault("pending_ui_intents", []).append(late)
            return fresh

        FilesystemMetadataStore(vault).update(_SID, meanwhile)
        if ok:
            return SimpleNamespace(ok=True, wa_message_id="wamid.photo", error=None)
        return SimpleNamespace(ok=False, wa_message_id=None, error="131047")

    monkeypatch.setattr(wa_client, "send_image", AsyncMock(side_effect=slow_send))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        pytest.param({}, id="bot"),
        pytest.param({"only_ids": {"ui_photo"}, "operator_tool": "present_product_detail"}, id="operator"),
    ],
)
async def test_what_others_save_while_a_send_is_in_flight_survives_the_flush(
    vault: Path, monkeypatch: pytest.MonkeyPatch, scope: dict[str, Any]
) -> None:
    _seed(vault)
    late = {"id": "ui_late", "kind": "shipping_rates", "params": {}, "queued_at_ms": _now()}
    _others_write_during_the_send(vault, monkeypatch, late)

    sent = await flush_ui_intents.flush_pending_ui_intents(_SID, **scope)

    assert sent == 1
    meta = _meta(vault)
    assert meta["last_inbound_at_ms"] == _T1  # el mensaje del cliente no se borró
    assert meta["pending_ui_intents"] == [late]  # lo encolado mientras tanto sigue; lo enviado salió
    # lo que el flush sí escribe quedó sobre el metadata fresco
    assert list(meta["outbound_media_index"]) == ["wamid.photo"]
    assert [e["event_name"] for e in meta["capi_outbox"]] == ["ViewContent"]


@pytest.mark.asyncio
async def test_a_rejected_send_drops_only_its_intent_and_keeps_what_was_saved_meanwhile(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(vault)
    late = {"id": "ui_late", "kind": "shipping_rates", "params": {}, "queued_at_ms": _now()}
    _others_write_during_the_send(vault, monkeypatch, late, ok=False)

    assert await flush_ui_intents.flush_pending_ui_intents(_SID) == 0

    meta = _meta(vault)
    assert meta["last_inbound_at_ms"] == _T1
    assert meta["pending_ui_intents"] == [late]
    assert meta["ui_intents_failures"] == [{"kind": "product_detail", "error": "131047"}]


@pytest.mark.asyncio
async def test_a_legacy_intent_without_id_goes_out_once_even_if_a_stale_write_brings_it_back(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una foto encolada antes de que los intents tuvieran id, y una escritura
    vieja que la devuelve a la cola mientras se envía (incidente 2026-10-06, #393):
    sale UNA vez; el registro de entregas frena la copia."""
    from src.platform.whatsapp import client as wa_client

    legacy = {k: v for k, v in _photo().items() if k != "id"}
    _seed(vault, pending_ui_intents=[legacy])
    _others_write_during_the_send(vault, monkeypatch, dict(legacy))

    assert await flush_ui_intents.flush_pending_ui_intents(_SID) == 1
    assert await flush_ui_intents.flush_pending_ui_intents(_SID) == 0

    assert wa_client.send_image.await_count == 1
    meta = _meta(vault)
    assert meta["pending_ui_intents"] == []
    assert meta["last_inbound_at_ms"] == _T1  # el mensaje del cliente no se borró


def test_the_flow_flag_is_written_under_the_metadata_lock(vault: Path) -> None:
    """Run 01a0a0f1 visto desde el otro lado: el flag del Flow se escribía sin
    lock, y un `update()` del ingest en vuelo lo pisaba al guardar su copia →
    el timeout extendido de ghosting no aplicaba."""
    (vault / _SID).mkdir(parents=True)
    (vault / _SID / "metadata.json").write_text(
        json.dumps({"phone_number_id": "pnid-1", "last_inbound_at_ms": _T0}), encoding="utf-8"
    )
    inside = threading.Event()

    def ingest(fresh: dict[str, Any]) -> dict[str, Any]:
        inside.set()
        time.sleep(0.3)  # el ingest sigue con el lock tomado
        fresh["last_inbound_at_ms"] = _T1
        return fresh

    writer = threading.Thread(target=FilesystemMetadataStore(vault).update, args=(_SID, ingest))
    writer.start()
    assert inside.wait(5)

    flush_ui_intents._mark_flow_awaiting_reply(_SID.removeprefix("wa_"))
    writer.join(5)

    meta = _meta(vault)
    assert meta["last_inbound_at_ms"] == _T1
    assert isinstance(meta.get("shipping_flow_awaiting_reply_since_ms"), int), meta
