"""Los escritores de `platform/` que esperan a Meta no pisan lo que otro
escritor puso mientras tanto (incidente 2026-10-06).

Cada uno leía `metadata.json`, esperaba la respuesta de Meta (el texto del bot
con 1,5 s entre burbujas, una plantilla, el POST de CAPI) y escribía su copia
ENTERA: lo que el flush, el ingest o el operador escribieron en esa espera se
perdía — así volvió a la cola una foto ya entregada. Ahora escriben solo lo
suyo, sobre la lectura fresca y con el candado del store.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp import activities as wa_activities
from src.platform.whatsapp.capi_outbox import CapiConfig, flush_capi_outbox
from src.platform.whatsapp.dtos import OutboundResult

SESSION = "wa_573001234567"
NOW_MS = 1_757_350_000_000


def _seed(vault: Path, metadata: dict[str, Any]) -> Path:
    path = vault / SESSION / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return path


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _flush_pops_the_photo(vault: Path) -> None:
    """Lo que hace el flush de tarjetas mientras el otro escritor espera."""

    def _pop(fresh: dict[str, Any]) -> dict[str, Any]:
        fresh["pending_ui_intents"] = []
        fresh["outbound_media_index"] = {"wamid.foto": {"handle": "cubo-love"}}
        return fresh

    FilesystemMetadataStore(vault).update(SESSION, _pop)


def _chat(**extra: Any) -> dict[str, Any]:
    return {
        "phone_number_id": "PHONE_TEST",
        "episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}],
        "pending_ui_intents": [{"id": "foto-t4", "kind": "product_detail"}],
        **extra,
    }


@pytest.mark.asyncio
async def test_bot_text_send_keeps_what_the_flush_wrote_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE_TEST")
    monkeypatch.setattr(wa_activities, "WORKSPACE_VAULT_DIR", tmp_path)

    async def _no_sleep(_secs: float) -> None:
        return None

    monkeypatch.setattr(wa_activities.asyncio, "sleep", _no_sleep)
    path = _seed(tmp_path, _chat())

    async def send_text(*args: Any, **kwargs: Any) -> OutboundResult:
        _flush_pops_the_photo(tmp_path)
        return OutboundResult(wa_message_id="wamid.texto", ok=True)

    monkeypatch.setattr(wa_activities.whatsapp_client, "send_text", send_text)

    assert await wa_activities.send_message_to_session(SESSION, "Claro, aquí la tienes") is True

    metadata = _read(path)
    assert metadata["pending_ui_intents"] == [], "el envío del texto devolvió la foto a la cola"
    assert "wamid.foto" in metadata["outbound_media_index"]
    assert metadata["last_outbound"]["wa_message_id"] == "wamid.texto"
    assert metadata["episodes"][0]["outbound_messages"][0]["wa_message_id"] == "wamid.texto"


@pytest.mark.asyncio
async def test_template_send_keeps_an_operator_takeover_made_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wa_activities, "WORKSPACE_VAULT_DIR", tmp_path)
    path = _seed(tmp_path, _chat(active_route="ventas"))

    async def send_template(*args: Any, **kwargs: Any) -> OutboundResult:
        FilesystemMetadataStore(tmp_path).update(SESSION, lambda d: {**d, "active_route": "humano"})
        return OutboundResult(wa_message_id="wamid.plantilla", ok=True)

    monkeypatch.setattr(wa_activities.whatsapp_client, "send_template", AsyncMock(side_effect=send_template))

    result = await wa_activities.send_template_to_session(
        SESSION, "quote_ready_utility_v2", {"product_or_quote_label": "vela"}
    )

    assert result.ok is True
    metadata = _read(path)
    assert metadata["active_route"] == "humano", "la plantilla pisó la toma del operador"
    assert metadata["last_outbound"]["wa_message_id"] == "wamid.plantilla"
    assert metadata["recent_template_sends"][-1]["wa_message_id"] == "wamid.plantilla"


@pytest.mark.asyncio
async def test_capi_flush_keeps_an_event_queued_during_the_post(tmp_path: Path) -> None:
    """Una tool encola `AddToCart` mientras el flusher espera a Meta por el
    `ViewContent`: el nuevo queda en el outbox para el próximo flush."""
    path = _seed(
        tmp_path,
        {
            "ctwa_referrals": [{"ctwa_clid": "CLID_1", "captured_at_ms": NOW_MS - 1000}],
            "episodes": [{"episode_id": "ep_001", "started_at_ms": NOW_MS - 5000}],
            "capi_outbox": [
                {"event_id": "viewcontent_1", "event_name": "ViewContent", "episode_id": "ep_001",
                 "queued_at_ms": NOW_MS, "attempts": 0, "source": "flush_ui_intents:product_detail"},
            ],
        },
    )
    late = {"event_id": "addtocart_1", "event_name": "AddToCart", "episode_id": "ep_001",
            "queued_at_ms": NOW_MS, "attempts": 0, "source": "flush_ui_intents:order_confirmation"}

    async def post(url: str, body: dict[str, Any], token: str) -> httpx.Response:
        FilesystemMetadataStore(tmp_path).update(
            SESSION, lambda d: {**d, "capi_outbox": [*d["capi_outbox"], late]}
        )
        return httpx.Response(200, json={"events_received": 1})

    config = CapiConfig(dataset_id="DS1", access_token="TOK", waba_id="WABA1", test_event_code="", vault_dir=tmp_path)
    result = await flush_capi_outbox(SESSION, config=config, post=post, now_ms=NOW_MS)

    assert result.sent == 1
    metadata = _read(path)
    assert [e["event_id"] for e in metadata["capi_outbox"]] == ["addtocart_1"], "se perdió el evento encolado"
    assert [(e["event_id"], e["status"]) for e in metadata["capi_events_sent"]] == [("viewcontent_1", "sent")]


# --- revisión del PR #393 (M7) -------------------------------------------------


def _mark_paid(fresh: dict[str, Any]) -> bool:
    fresh["tag"] = "COMPRA_EXITOSA"
    return True


def test_the_medusa_sync_never_creates_a_chat_that_does_not_exist(tmp_path: Path) -> None:
    """La confirmación de pago desde Órdenes sincroniza el chat SOLO si existe:
    si el archivo desapareció entre la revisión y el candado, no lo crea."""
    from src.platform.orders.medusa_order_command import _apply_to_chat_metadata

    path = tmp_path / SESSION / "metadata.json"

    assert _apply_to_chat_metadata(path, _mark_paid) is False
    assert not path.exists()


def test_the_medusa_sync_never_writes_over_an_unreadable_chat(tmp_path: Path) -> None:
    from src.platform.orders.medusa_order_command import _apply_to_chat_metadata

    path = tmp_path / SESSION / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"active_route": "humano", "episodes": [', encoding="utf-8")
    before = path.read_bytes()

    assert _apply_to_chat_metadata(path, _mark_paid) is False
    assert path.read_bytes() == before
