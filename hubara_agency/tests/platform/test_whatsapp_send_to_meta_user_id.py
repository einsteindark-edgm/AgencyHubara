"""A un cliente sin teléfono (nombre de usuario de WhatsApp) se le contesta
con `recipient` = su id de Meta (BSUID); a uno con teléfono, con `to` como
siempre. La traducción la hace el cliente HTTP en el ÚNICO punto por donde
sale todo envío (`_post_json`): ningún caller arma ese campo, así que el
texto del bot, los botones, las fotos, las plantillas y el operador humano
llegan a los dos tipos de cliente.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import src.platform.whatsapp.client as wa_client
from src.platform.whatsapp import dtos as wa_dtos


def _ok() -> httpx.Response:
    return httpx.Response(200, json={"messages": [{"id": "wamid.OUT"}]}, request=httpx.Request("POST", "https://x"))


async def _sent_payload(monkeypatch, send) -> dict[str, Any]:
    monkeypatch.setattr(wa_client, "WHATSAPP_ACCESS_TOKEN", "tok")
    post = AsyncMock(return_value=_ok())
    with patch.object(httpx.AsyncClient, "post", new=post):
        result = await send()
    assert result is None or result.ok
    return post.call_args.kwargs["json"]


@pytest.mark.asyncio
async def test_text_to_a_customer_without_phone_goes_to_their_meta_id(monkeypatch) -> None:
    payload = await _sent_payload(monkeypatch, lambda: wa_client.send_text("PNID", "CO1502576394655843", "hola"))

    assert payload["recipient"] == "CO.1502576394655843"
    assert "to" not in payload


@pytest.mark.asyncio
async def test_text_to_a_phone_customer_still_goes_to_their_phone(monkeypatch) -> None:
    payload = await _sent_payload(monkeypatch, lambda: wa_client.send_text("PNID", "573001234567", "hola"))

    assert payload["to"] == "573001234567"
    assert "recipient" not in payload


@pytest.mark.asyncio
async def test_the_legacy_text_send_also_reaches_a_customer_without_phone(monkeypatch) -> None:
    payload = await _sent_payload(monkeypatch, lambda: wa_client.send_message("PNID", "CO1502576394655843", "hola"))

    assert payload["recipient"] == "CO.1502576394655843"
    assert "to" not in payload


@pytest.mark.asyncio
async def test_buttons_reach_a_customer_without_phone(monkeypatch) -> None:
    buttons = wa_dtos.InteractiveButtonsOutbound(
        body="¿Cuál te gusta?", buttons=[wa_dtos.ReplyButton(id="a", title="A")]
    )
    payload = await _sent_payload(
        monkeypatch, lambda: wa_client.send_interactive_buttons("PNID", "CO1502576394655843", buttons)
    )

    assert payload["recipient"] == "CO.1502576394655843"
    assert "to" not in payload
