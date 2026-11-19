"""El nombre que ve el panel (Orders y el reporte de ventas) es el de quien
compró, no el relleno con el que el bot crea al cliente en la tienda.

Caso real (2026-11-19): 75 de 110 pedidos del reporte decían «Cliente
WhatsApp». El bot registra al cliente como «Cliente WhatsApp» y el nombre
que la persona escribe en el formulario queda en la dirección de envío; el
resumen preferia el del cliente. `customer` NO cambia (lo leen el saludo de
ETA y los facts del pedido, que ya saltan el relleno): el panel usa
`display_customer`. Datos inventados.
"""

from __future__ import annotations

import pytest
import respx

from tests.platform.orders.test_medusa_order_query import (
    _list_single,
    _sample_order,
    adapter,  # noqa: F401  (fixture)
)


def _bot_order(*, customer: tuple[str, str], receiver: tuple[str, str]) -> dict:
    raw = _sample_order()
    raw["customer"] = {"first_name": customer[0], "last_name": customer[1]}
    raw["shipping_address"]["first_name"] = receiver[0]
    raw["shipping_address"]["last_name"] = receiver[1]
    return raw


async def _summary(adapter, raw: dict):  # noqa: F811
    await _list_single(raw)
    result = await adapter.list(limit=50, offset=0, include_drafts=True)
    return result.orders[0]


@pytest.mark.asyncio
@respx.mock
async def test_bot_customer_shows_the_name_from_the_shipping_form(adapter):  # noqa: F811
    s = await _summary(adapter, _bot_order(customer=("Cliente", "WhatsApp"), receiver=("Ana", "Pérez")))

    assert s.display_customer == "Ana Pérez"
    assert s.short == "AP"
    # El saludo de ETA y los facts siguen leyendo el mismo `customer`.
    assert s.customer == "Cliente WhatsApp"


@pytest.mark.asyncio
@respx.mock
async def test_a_real_customer_name_wins_over_who_receives(adapter):  # noqa: F811
    s = await _summary(adapter, _bot_order(customer=("María", "Camila"), receiver=("Portería", "")))

    assert s.display_customer == "María Camila"


@pytest.mark.asyncio
@respx.mock
async def test_without_any_real_name_it_keeps_what_it_showed_before(adapter):  # noqa: F811
    s = await _summary(adapter, _bot_order(customer=("Cliente", "WhatsApp"), receiver=("Cliente", "WhatsApp")))

    assert s.display_customer == "Cliente WhatsApp"
    assert s.display_customer == s.customer
