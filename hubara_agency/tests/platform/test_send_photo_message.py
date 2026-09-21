"""``send_photo_message_to_session`` — foto con texto como mensaje NORMAL.

Dentro de la ventana de servicio 24h no hace falta plantilla: la foto del pedido
listo sale como mensaje de imagen con caption (más barato que la plantilla
hasta el 30-sep-2026; desde el 1-oct Meta cobra igual, pero sigue sin depender
de la aprobación de la plantilla). A diferencia de ``send_image_to_session``
(que usa el dashboard del operador, que escribe su propio historial), esta la
usan los agentes: deja el mensaje en el historial del chat con la foto.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.platform.whatsapp.dtos import OutboundResult
from src.sdk.messagingkit import is_in_service_window, send_photo_message_to_session

SID = "wa_573001234567"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr("src.platform.whatsapp.activities.WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE_ENV")
    (tmp_path / SID).mkdir()
    (tmp_path / SID / "metadata.json").write_text(json.dumps({"phone_number_id": "P"}), encoding="utf-8")
    return tmp_path


def _history(vault: Path) -> list[dict]:
    path = vault / SID / "sessions" / f"{SID}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_sends_image_with_caption_and_keeps_it_in_the_chat(vault, monkeypatch):
    send = AsyncMock(return_value=OutboundResult(wa_message_id="wamid.I", ok=True))
    monkeypatch.setattr("src.platform.whatsapp.activities.whatsapp_client.send_image", send)

    result = await send_photo_message_to_session(
        SID, "MEDIA_1", "Tu pedido #31 ya está listo.", image_url="/api/dashboard/media/wa_x/f.jpg"
    )

    assert result.ok
    _, to, payload = send.await_args.args
    assert to == "573001234567"
    assert (payload.media_id, payload.caption) == ("MEDIA_1", "Tu pedido #31 ya está listo.")
    [event] = _history(vault)
    assert event["role"] == "assistant"
    assert event["content"] == "Tu pedido #31 ya está listo."
    assert event["image_url"] == "/api/dashboard/media/wa_x/f.jpg"
    assert event["wamid"] == "wamid.I"


async def test_a_rejected_send_leaves_no_trace_in_the_chat(vault, monkeypatch):
    send = AsyncMock(return_value=OutboundResult(wa_message_id=None, ok=False, error='{"code":131047}'))
    monkeypatch.setattr("src.platform.whatsapp.activities.whatsapp_client.send_image", send)

    result = await send_photo_message_to_session(SID, "MEDIA_1", "hola", image_url=None)

    assert not result.ok
    assert _history(vault) == []


def test_service_window_check_is_strict_about_unknown_windows():
    # Sin dato de ventana NO se asume abierta: mejor plantilla que un rechazo.
    assert is_in_service_window(1_000, {}) is False
    assert is_in_service_window(1_000, {"service_window_expires_at_ms": 2_000}) is True
    assert is_in_service_window(3_000, {"service_window_expires_at_ms": 2_000}) is False
