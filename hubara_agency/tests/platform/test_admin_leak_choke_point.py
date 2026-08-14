"""Tripwire B9 (premortem run 5f43bcd0): última línea en el choke point.

`send_whatsapp_message_activity` es el único camino por el que el texto
GENERADO POR LLM de cualquier workflow (sales, remarketing, los que vengan)
llega a WhatsApp. Si todos los guards del workflow fallan (una confluencia
de flags nueva, un workflow futuro sin guards), el texto administrativo se
bloquea acá — determinista, sin LLM auditor.

El path del operador humano (dashboard → `send_message_to_session` directo)
NO pasa por esta activity y no se ve afectado.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.testing import ActivityEnvironment

import src.platform.whatsapp.activities as activities
from src.platform.whatsapp.dtos import OutboundResult


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(activities, "WORKSPACE_VAULT_DIR", tmp_path, raising=True)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "phone-test")
    return tmp_path


def _seed(vault, session_id: str) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps({}), encoding="utf-8")


@pytest.mark.asyncio
async def test_admin_text_is_blocked_at_worker_choke_point(vault) -> None:
    _seed(vault, "wa_choke")
    send_text = AsyncMock(
        return_value=OutboundResult(wa_message_id="wamid.1", ok=True)
    )
    with (
        patch.object(activities.whatsapp_client, "send_text", new=send_text),
        patch.object(activities.asyncio, "sleep", new=AsyncMock()),
    ):
        env = ActivityEnvironment()
        await env.run(
            activities.send_whatsapp_message_activity,
            "wa_choke",
            "La conversación queda etiquetada como `INTERESADO`. "
            "Remarketing automático activado.",
        )
    assert send_text.await_count == 0, (
        "texto administrativo NO debe llegar a la API de WhatsApp"
    )


@pytest.mark.asyncio
async def test_legit_text_passes_choke_point(vault) -> None:
    _seed(vault, "wa_choke2")
    send_text = AsyncMock(
        return_value=OutboundResult(wa_message_id="wamid.2", ok=True)
    )
    with (
        patch.object(activities.whatsapp_client, "send_text", new=send_text),
        patch.object(activities.asyncio, "sleep", new=AsyncMock()),
    ):
        env = ActivityEnvironment()
        await env.run(
            activities.send_whatsapp_message_activity,
            "wa_choke2",
            "¡Hola! ¿Qué aroma te gustaría para tu vela?",
        )
    assert send_text.await_count == 1, (
        "un mensaje legítimo debe salir normalmente"
    )
