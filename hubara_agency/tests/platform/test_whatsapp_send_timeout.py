"""PM2-B1b: `_post_json` distingue TIMEOUT de otros fallos de transporte.

Un timeout es AMBIGUO: Meta puede haber entregado el mensaje y la respuesta se
perdió (lección L-1). El error viene prefijado `timeout:` para que el caller
(handoff) responda 504 "puede haberse entregado" en vez de 502 "rechazado" —
sin esto el frontend reintenta "seguro" y el cliente recibe la foto DUPLICADA.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

import src.platform.whatsapp.client as wa_client


@pytest.mark.asyncio
async def test_post_json_timeout_is_prefixed(monkeypatch):
    monkeypatch.setattr(wa_client, "WHATSAPP_ACCESS_TOKEN", "tok")

    with patch.object(
        httpx.AsyncClient,
        "post",
        new=AsyncMock(side_effect=httpx.ReadTimeout("read timeout")),
    ):
        result = await wa_client._post_json("phone-1", {"x": 1}, label="image")

    assert result.ok is False
    assert (result.error or "").startswith("timeout")


@pytest.mark.asyncio
async def test_post_json_connect_error_is_not_timeout(monkeypatch):
    monkeypatch.setattr(wa_client, "WHATSAPP_ACCESS_TOKEN", "tok")

    with patch.object(
        httpx.AsyncClient,
        "post",
        new=AsyncMock(side_effect=httpx.ConnectError("refused")),
    ):
        result = await wa_client._post_json("phone-1", {"x": 1}, label="image")

    assert result.ok is False
    assert not (result.error or "").startswith("timeout")
