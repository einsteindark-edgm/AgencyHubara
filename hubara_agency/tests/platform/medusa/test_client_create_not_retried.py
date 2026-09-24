"""Crear un draft o un cliente NO se reintenta si el POST pudo llegar (L-27).

Un POST que se cortó por timeout (o con la conexión rota a mitad de la
respuesta) PUDO haberse aplicado en Medusa: reintentarlo crea un SEGUNDO
draft (el cliente pagaría dos pedidos) o un cliente duplicado. Solo se
reintenta si la conexión ni siquiera se abrió (`ConnectError`), igual que las
escrituras de promociones.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from src.platform.medusa.client import HttpMedusaClient

_BASE = "https://m.test"


async def _create_draft(c: HttpMedusaClient) -> dict:
    return await c.create_draft_order({"email": "x@y.co", "items": []})


async def _create_customer(c: HttpMedusaClient) -> dict:
    return await c.create_customer(email="x@y.co")


_CREATES = [
    ("/admin/draft-orders", _create_draft, {"draft_order": {"id": "dord_1"}}),
    ("/admin/customers", _create_customer, {"customer": {"id": "cus_1"}}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,create,_ok", _CREATES)
@pytest.mark.parametrize(
    "cut", [httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("peer closed")]
)
async def test_create_post_is_not_retried_after_it_may_have_landed(path, create, _ok, cut) -> None:
    c = HttpMedusaClient(base_url=_BASE, admin_token="sk_x")
    with respx.mock(base_url=_BASE) as r:
        route = r.post(path).mock(side_effect=cut)
        with pytest.raises(type(cut)):
            await create(c)
        assert route.call_count == 1
    await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("path,create,ok", _CREATES)
async def test_create_post_is_retried_when_the_connection_never_opened(path, create, ok) -> None:
    c = HttpMedusaClient(base_url=_BASE, admin_token="sk_x")
    with respx.mock(base_url=_BASE) as r:
        route = r.post(path).mock(
            side_effect=[httpx.ConnectError("refused"), httpx.Response(200, json=ok)]
        )
        created = await create(c)
        assert route.call_count == 2
    await c.aclose()

    assert created["id"] in ("dord_1", "cus_1")
