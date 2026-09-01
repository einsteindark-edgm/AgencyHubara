"""`send_document_to_session` — envío de PDFs (comprobantes) del operador.

Espejo de `send_image_to_session` (ver test_send_image_outbound_log.py) para
`type=document` de WhatsApp: mismo resolver de phone_number_id, mismo registro
del outbound en metadata (cost-tracking / analytics / lead_state.engaged), con
`kind="document"` y el `filename` visible para el cliente en la burbuja de
WhatsApp.
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
async def test_send_document_passes_media_id_caption_and_filename(vault):
    """El payload a Meta lleva media_id + caption + filename — el filename es
    lo que el cliente ve como nombre del archivo en WhatsApp."""
    _seed(vault, "wa_doc", {"episodes": [{"episode_id": "e1", "closed_at_ms": None}]})

    ok = OutboundResult(wa_message_id="wamid.doc", ok=True)
    send_mock = AsyncMock(return_value=ok)
    with patch.object(activities.whatsapp_client, "send_document", new=send_mock):
        result = await activities.send_document_to_session(
            "wa_doc",
            media_id="m-pdf-1",
            caption="Comprobante adjunto",
            filename="comprobante.pdf",
        )

    assert result.ok is True
    send_mock.assert_awaited_once()
    payload = send_mock.await_args.args[2]
    assert payload.media_id == "m-pdf-1"
    assert payload.caption == "Comprobante adjunto"
    assert payload.filename == "comprobante.pdf"


@pytest.mark.asyncio
async def test_send_document_appends_outbound_log_with_kind_document(vault):
    _seed(vault, "wa_doc2", {"episodes": [{"episode_id": "e1", "closed_at_ms": None}]})

    ok = OutboundResult(wa_message_id="wamid.d2", ok=True)
    with patch.object(
        activities.whatsapp_client, "send_document", new=AsyncMock(return_value=ok)
    ):
        result = await activities.send_document_to_session(
            "wa_doc2", media_id="m-2", caption=None, filename="doc.pdf"
        )

    assert result.ok is True
    data = _read(vault, "wa_doc2")
    assert data["last_outbound"]["kind"] == "document"
    outbound = data["episodes"][-1]["outbound_messages"]
    assert len(outbound) == 1
    assert outbound[0]["kind"] == "document"


@pytest.mark.asyncio
async def test_send_document_failed_does_not_log_outbound(vault):
    _seed(vault, "wa_doc3", {"episodes": [{"episode_id": "e", "closed_at_ms": None}]})

    fail = OutboundResult(wa_message_id=None, ok=False, error="http_400: bad")
    with patch.object(
        activities.whatsapp_client, "send_document", new=AsyncMock(return_value=fail)
    ):
        result = await activities.send_document_to_session(
            "wa_doc3", media_id="m-3", caption=None, filename=None
        )

    assert result.ok is False
    data = _read(vault, "wa_doc3")
    assert "last_outbound" not in data
