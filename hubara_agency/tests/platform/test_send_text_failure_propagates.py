"""PM2-B3: `send_message_to_session` propaga el rechazo de Meta.

Antes usaba el legacy `send_message` (swallow: loguea y devuelve None) — si
Meta rechazaba (ventana cerrada sin metadata poblada, número inválido, token
vencido) el operador veía "enviado" y el cliente nunca recibía nada. Ahora
devuelve True/False y ante el primer chunk fallido CORTA.
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
    return json.loads(
        (vault / session_id / "metadata.json").read_text(encoding="utf-8")
    )


@pytest.mark.asyncio
async def test_send_text_ok_returns_true(vault):
    _seed(vault, "wa_t1", {})
    ok = OutboundResult(wa_message_id="wamid.1", ok=True)
    with (
        patch.object(
            activities.whatsapp_client, "send_text", new=AsyncMock(return_value=ok)
        ),
        patch.object(activities.asyncio, "sleep", new=AsyncMock()),
    ):
        result = await activities.send_message_to_session("wa_t1", "hola")
    assert result is True
    assert _read(vault, "wa_t1")["last_outbound"]["kind"] == "text"


@pytest.mark.asyncio
async def test_send_text_rejection_returns_false_and_does_not_mark(vault):
    """Rechazo total (primer chunk): False, y NO se registra fingerprint ni
    last_outbound (no se entregó nada → el retry debe poder reintentar)."""
    _seed(vault, "wa_t2", {})
    fail = OutboundResult(wa_message_id=None, ok=False, error="http_400: bad")
    with (
        patch.object(
            activities.whatsapp_client, "send_text", new=AsyncMock(return_value=fail)
        ),
        patch.object(activities.asyncio, "sleep", new=AsyncMock()),
    ):
        result = await activities.send_message_to_session("wa_t2", "hola")
    assert result is False
    assert "last_outbound" not in _read(vault, "wa_t2")


@pytest.mark.asyncio
async def test_send_text_partial_failure_stops_and_marks_fingerprint(vault):
    """Fallo en el chunk 2 de 3: se corta (no se manda el 3ro), devuelve False,
    pero SÍ registra el fingerprint — un retry idéntico no re-manda el chunk
    que ya llegó al cliente."""
    _seed(vault, "wa_t3", {})
    results = [
        OutboundResult(wa_message_id="wamid.a", ok=True),
        OutboundResult(wa_message_id=None, ok=False, error="http_500: down"),
    ]
    send_mock = AsyncMock(side_effect=results)
    with (
        patch.object(activities.whatsapp_client, "send_text", new=send_mock),
        patch.object(activities.asyncio, "sleep", new=AsyncMock()),
    ):
        result = await activities.send_message_to_session(
            "wa_t3", "uno\n\ndos\n\ntres"
        )
    assert result is False
    assert send_mock.await_count == 2  # el tercer chunk NO se intentó
    data = _read(vault, "wa_t3")
    assert data["last_outbound"]["kind"] == "text"  # entrega parcial registrada

    # Retry idéntico → dedup por fingerprint (True, sin re-enviar).
    send_mock2 = AsyncMock()
    with patch.object(activities.whatsapp_client, "send_text", new=send_mock2):
        retry = await activities.send_message_to_session(
            "wa_t3", "uno\n\ndos\n\ntres"
        )
    assert retry is True
    send_mock2.assert_not_awaited()
