"""Tests de `send_typing_indicator_activity` (Fix 5).

Verifica:
  * Si hay `last_inbound_message_id` en metadata, se invoca el client.
  * Si no hay message_id, la activity noopea silenciosamente.
  * Si no hay metadata.json (sesion nueva), tambien noop.
"""
from __future__ import annotations

import json

import pytest
from temporalio.testing import ActivityEnvironment


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Redirige `WORKSPACE_VAULT_DIR` en activities.py a un tmp."""
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.WORKSPACE_VAULT_DIR",
        tmp_path,
    )
    return tmp_path


@pytest.fixture
def mock_typing_call(monkeypatch):
    """Captura las llamadas a `whatsapp_client.send_typing_indicator`."""
    calls: list[dict] = []

    async def fake_send_typing(phone_number_id: str, message_id: str) -> None:
        calls.append({"pni": phone_number_id, "msg_id": message_id})

    monkeypatch.setattr(
        "src.platform.whatsapp.activities.whatsapp_client.send_typing_indicator",
        fake_send_typing,
    )
    return calls


async def test_typing_dispatched_when_message_id_present(
    vault, mock_typing_call, monkeypatch
) -> None:
    """Fix 5 happy path: con message_id en metadata se llama al client."""
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PNI_DEFAULT")

    session_id = "wa_5491111111111"
    (vault / session_id).mkdir(parents=True)
    (vault / session_id / "metadata.json").write_text(json.dumps({
        "phone_number_id": "PNI_SESSION",
        "last_inbound_message_id": "wamid.ABC123",
    }))

    from src.platform.whatsapp.activities import send_typing_indicator_activity
    env = ActivityEnvironment()
    await env.run(send_typing_indicator_activity, session_id)

    assert len(mock_typing_call) == 1
    assert mock_typing_call[0]["pni"] == "PNI_SESSION"
    assert mock_typing_call[0]["msg_id"] == "wamid.ABC123"


async def test_typing_skipped_when_no_message_id(
    vault, mock_typing_call, monkeypatch
) -> None:
    """Sin message_id no hay typing — la API de WhatsApp lo requiere."""
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PNI_DEFAULT")

    session_id = "wa_no_msg_id"
    (vault / session_id).mkdir(parents=True)
    (vault / session_id / "metadata.json").write_text(json.dumps({
        "phone_number_id": "PNI_DEFAULT",
    }))

    from src.platform.whatsapp.activities import send_typing_indicator_activity
    env = ActivityEnvironment()
    await env.run(send_typing_indicator_activity, session_id)

    assert mock_typing_call == []


async def test_typing_skipped_when_metadata_missing(
    vault, mock_typing_call, monkeypatch
) -> None:
    """Sesion nueva sin metadata.json — graceful skip."""
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PNI_DEFAULT")

    from src.platform.whatsapp.activities import send_typing_indicator_activity
    env = ActivityEnvironment()
    await env.run(send_typing_indicator_activity, "wa_sin_metadata")

    assert mock_typing_call == []


async def test_typing_uses_env_pni_when_not_in_metadata(
    vault, mock_typing_call, monkeypatch
) -> None:
    """Fallback al env var si metadata no tiene phone_number_id."""
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PNI_ENV")

    session_id = "wa_solo_msg_id"
    (vault / session_id).mkdir(parents=True)
    (vault / session_id / "metadata.json").write_text(json.dumps({
        "last_inbound_message_id": "wamid.XYZ",
    }))

    from src.platform.whatsapp.activities import send_typing_indicator_activity
    env = ActivityEnvironment()
    await env.run(send_typing_indicator_activity, session_id)

    assert mock_typing_call == [{"pni": "PNI_ENV", "msg_id": "wamid.XYZ"}]
