"""Guard de texto client-facing en el flush de UI intents (run 1c9ef231).

Los UI intents NO pasan por `send_whatsapp_message_activity` — su texto
(`intro_text`, `body`, `caption`...) lo escribe el LLM en los params de la
tool y viaja VERBATIM al cliente vía `_dispatch_intent`. Era el único path
LLM→cliente sin ningún guard determinista (ni sanitizer ni
`looks_like_admin_leak`).

Contrato: `_sanitize_intent_client_text(kind, params)` limpia cada param de
texto con `sanitize_llm_text` y, si huele a reporte administrativo
(`looks_like_admin_leak`), lo reemplaza por un neutro determinista — el
intent SIGUE saliendo (el menú/botones son el canal legítimo), solo muere el
texto envenenado. Un falso positivo acá NO deja al cliente mudo: degrada a
un intro genérico.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    _dispatch_intent,
    _sanitize_intent_client_text,
)
from src.platform.whatsapp import dtos as wa_dtos


# --- Función pura -----------------------------------------------------------


def test_poisoned_intro_text_is_replaced_with_neutral() -> None:
    params = {
        "intro_text": "Encontré 10 velas religiosas. Las muestro al cliente.",
        "sections": [{"title": "Religiosas", "rows": []}],
    }
    out = _sanitize_intent_client_text("products_list", params)
    assert "al cliente" not in out["intro_text"]
    assert out["intro_text"]  # hay un intro neutro, el menú sale igual
    # Los params no-texto quedan intactos.
    assert out["sections"] == params["sections"]


def test_poisoned_body_is_replaced_with_neutral() -> None:
    params = {
        "body": "Etiqueté como INTERESADO. El control ha sido transferido.",
        "buttons": [{"id": "catalog.browse", "title": "Ver catálogo"}],
    }
    out = _sanitize_intent_client_text("quick_replies", params)
    assert "INTERESADO" not in out["body"]
    assert out["body"]
    assert out["buttons"] == params["buttons"]


def test_clean_text_passes_untouched() -> None:
    params = {
        "intro_text": "Estas son nuestras velas religiosas:",
        "body": "¿Cuál te llama la atención?",
    }
    out = _sanitize_intent_client_text("products_list", params)
    assert out["intro_text"] == "Estas son nuestras velas religiosas:"
    assert out["body"] == "¿Cuál te llama la atención?"


def test_wrapping_quotes_are_stripped() -> None:
    # sanitize_llm_text también corre acá: mismo cleanup que el path de texto.
    params = {"intro_text": '"Estas son nuestras velas:"'}
    out = _sanitize_intent_client_text("products_list", params)
    assert out["intro_text"] == "Estas son nuestras velas:"


def test_missing_and_none_keys_are_tolerated() -> None:
    params = {"sections": [], "body": None}
    out = _sanitize_intent_client_text("products_list", params)
    assert out["sections"] == []
    assert out["body"] is None


# --- Wiring: el guard corre dentro de _dispatch_intent ----------------------


@pytest.mark.asyncio
async def test_dispatch_neutralizes_poisoned_intro(monkeypatch) -> None:
    monkeypatch.delenv("META_CATALOG_ID", raising=False)
    wa_client = SimpleNamespace(
        send_interactive_list=AsyncMock(return_value=SimpleNamespace(ok=True)),
    )
    result = await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="products_list",
        params={
            "intro_text": (
                "Encontré 10 velas religiosas. Las muestro al cliente."
            ),
            "sections": [
                {
                    "title": "Religiosas",
                    "rows": [
                        {"id": "luz-serena", "title": "Luz Serena"},
                    ],
                }
            ],
        },
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )
    assert result is not None and result.ok is True
    payload = wa_client.send_interactive_list.call_args.args[2]
    assert "al cliente" not in payload.body
    assert payload.body  # el menú salió con un intro neutro
