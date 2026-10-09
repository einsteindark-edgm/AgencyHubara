"""Un envío que Meta rechaza por una falla PASAJERA se reintenta una vez
(premortem 2026-10-09).

Todo envío sale por `_post_json`: el texto del bot, el formulario, la tarjeta
del pedido y los datos de pago. Un 5xx de Meta o una conexión caída dejaban
al cliente sin los datos de pago (el flush los saca de la cola sin
reintentar) o sin la última burbuja del bot, mientras el panel y la memoria
del LLM los daban por enviados. Se reintenta UNA vez lo que seguro no salió
(5xx, conexión). Un timeout no: Meta pudo haberlo entregado y se duplicaría.
Un 4xx tampoco: es un rechazo de verdad (plantilla, número, ventana).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

import src.platform.whatsapp.client as wa_client


def _response(status: int) -> httpx.Response:
    body = {"messages": [{"id": "wamid.OUT"}]} if status == 200 else {"error": {"code": 131000}}
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://x"))


async def _send(monkeypatch, *outcomes) -> tuple[object, AsyncMock]:
    monkeypatch.setattr(wa_client, "WHATSAPP_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(wa_client, "_RETRY_DELAY_S", 0)
    post = AsyncMock(side_effect=list(outcomes))
    with patch.object(httpx.AsyncClient, "post", new=post):
        result = await wa_client.send_text("PNID", "573001234567", "Los datos para el pago 🤍")
    return result, post


@pytest.mark.asyncio
async def test_a_meta_5xx_is_retried_once(monkeypatch) -> None:
    result, post = await _send(monkeypatch, _response(503), _response(200))

    assert result.ok and result.wa_message_id == "wamid.OUT"
    assert post.await_count == 2


@pytest.mark.asyncio
async def test_a_dropped_connection_is_retried_once(monkeypatch) -> None:
    result, post = await _send(monkeypatch, httpx.ConnectError("refused"), _response(200))

    assert result.ok
    assert post.await_count == 2


@pytest.mark.asyncio
async def test_a_second_failure_is_reported(monkeypatch) -> None:
    result, post = await _send(monkeypatch, _response(500), _response(502))

    assert not result.ok and result.error.startswith("http_502")
    assert post.await_count == 2


@pytest.mark.asyncio
async def test_a_timeout_is_not_retried(monkeypatch) -> None:
    result, post = await _send(monkeypatch, httpx.ReadTimeout("slow"))

    assert not result.ok and result.error.startswith("timeout:")
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_a_real_rejection_is_not_retried(monkeypatch) -> None:
    result, post = await _send(monkeypatch, _response(400))

    assert not result.ok and result.error.startswith("http_400")
    assert post.await_count == 1
