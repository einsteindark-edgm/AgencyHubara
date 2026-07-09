"""Sanitizador de formato: markdown del LLM → formato nativo de WhatsApp.

Caso real (sesión wa_573125671604): el LLM escribió `**Banco**: Bancolombia`
y el cliente vio los asteriscos dobles crudos — WhatsApp usa `*bold*` (un
asterisco) y `_italic_` (un guion bajo). `to_whatsapp_text` convierte de
forma determinista; `send_message_to_session` (el camino de TODAS las
burbujas de texto de los agentes) lo aplica antes de enviar.
"""
from __future__ import annotations

import pytest

from src.platform.whatsapp.formatting import to_whatsapp_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("**Banco**: Bancolombia", "*Banco*: Bancolombia"),
        (
            "Total: **$47.000** y **envío gratis**",
            "Total: *$47.000* y *envío gratis*",
        ),
        ("__cursiva__ marcada", "_cursiva_ marcada"),
        # Texto ya en formato WhatsApp NO se toca
        ("*Banco*: Bancolombia", "*Banco*: Bancolombia"),
        ("sin formato alguno", "sin formato alguno"),
        # Asterisco suelto (no par) se respeta
        ("2 * 3 = 6", "2 * 3 = 6"),
    ],
)
def test_to_whatsapp_text_converts_markdown_bold_italic(raw: str, expected: str):
    assert to_whatsapp_text(raw) == expected


@pytest.mark.asyncio
async def test_send_message_to_session_sanitizes_bubbles(tmp_path, monkeypatch):
    """El camino real de los mensajes del agente sanea el markdown antes
    de mandar cada burbuja."""
    from src.platform.whatsapp import activities as wa_activities

    sent: list[str] = []

    async def _fake_send(phone_number_id, to, text):
        sent.append(text)

    monkeypatch.setattr(
        wa_activities.whatsapp_client, "send_message", _fake_send
    )
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "phone-1")
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.WORKSPACE_VAULT_DIR", tmp_path
    )
    # Sin sleep de 1.5s por burbuja en el test
    async def _no_sleep(_secs):
        return None

    monkeypatch.setattr(wa_activities.asyncio, "sleep", _no_sleep)

    await wa_activities.send_message_to_session(
        "wa_573000000000",
        "Hola **cliente**\n\nTu total es **$47.000**",
    )

    assert sent == ["Hola *cliente*", "Tu total es *$47.000*"]
