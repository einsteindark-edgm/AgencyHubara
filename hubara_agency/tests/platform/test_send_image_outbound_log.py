"""PM-B10: `send_image_to_session` debe registrar el outbound en metadata.

El path de texto (`send_message_to_session`) persiste `OutboundLogEntry` +
`last_outbound` tras enviar — sin eso las fotos del operador son invisibles
para cost-tracking, analytics y `lead_state.engaged`. Simetría exigida acá.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

import src.platform.whatsapp.activities as activities
from src.platform.whatsapp.dtos import OutboundResult


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(activities, "WORKSPACE_VAULT_DIR", tmp_path, raising=True)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "phone-test")
    return tmp_path


def _seed(vault, session_id: str, data: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _read(vault, session_id: str) -> dict:
    return json.loads((vault / session_id / "metadata.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_send_image_appends_outbound_log_and_last_outbound(vault):
    _seed(
        vault,
        "wa_img",
        {"episodes": [{"episode_id": "ep_1", "closed_at_ms": None}]},
    )

    ok = OutboundResult(wa_message_id="wamid.777", ok=True)
    with patch.object(
        activities.whatsapp_client, "send_image", new=AsyncMock(return_value=ok)
    ):
        result = await activities.send_image_to_session(
            "wa_img", media_id="m-1", caption="hola"
        )

    assert result.ok is True
    data = _read(vault, "wa_img")
    # last_outbound registrado con kind=image.
    assert data["last_outbound"]["kind"] == "image"
    # Y el episodio activo tiene el outbound en su log.
    outbound = data["episodes"][-1]["outbound_messages"]
    assert len(outbound) == 1
    assert outbound[0]["kind"] == "image"


@pytest.mark.asyncio
async def test_send_image_failed_does_not_log_outbound(vault):
    """Si Meta rechaza, NO se registra outbound (no se envió nada)."""
    _seed(vault, "wa_img2", {"episodes": [{"episode_id": "e", "closed_at_ms": None}]})

    fail = OutboundResult(wa_message_id=None, ok=False, error="http_400: bad")
    with patch.object(
        activities.whatsapp_client, "send_image", new=AsyncMock(return_value=fail)
    ):
        result = await activities.send_image_to_session("wa_img2", media_id="m-2")

    assert result.ok is False
    data = _read(vault, "wa_img2")
    assert "last_outbound" not in data
