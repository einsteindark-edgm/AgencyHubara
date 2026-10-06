"""El dispatch de `products_list` debe caer a `interactive.list` (browse del
catálogo LOCAL) cuando el envío nativo `interactive.product_list` (Meta Catalog)
FALLA — no solo cuando se elige la lista de entrada.

Bug prod (2026-07-01, run 019f1b52): con el catálogo recién conectado al WABA,
Meta rechaza el product_list con `(#131009) product not found ... in catalog_id`
(los items aún no son "shoppables"). El código elegía native-vs-lista ANTES de
enviar; al fallar el native NO recuperaba → el cliente quedaba sin catálogo y el
bot lo marcaba ghosting. `shipping_flow` sí tiene esta recuperación; `products_list`
no la tenía.
"""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.activities.flush_ui_intents import _dispatch_intent
from src.platform.whatsapp import dtos as wa_dtos


class _Res:
    def __init__(self, ok: bool, error: str | None = None):
        self.ok = ok
        self.error = error
        self.wa_message_id = "wamid.test"


class _FakeWaClient:
    def __init__(self, *, product_list_ok: bool, list_ok: bool = True):
        self._pl_ok = product_list_ok
        self._list_ok = list_ok
        self.product_list_called = False
        self.list_called = False

    async def send_product_list(self, *a, **k):
        self.product_list_called = True
        return _Res(self._pl_ok, None if self._pl_ok else "(#131009) product not found")

    async def send_interactive_list(self, *a, **k):
        self.list_called = True
        return _Res(self._list_ok)


def _params() -> dict:
    return {
        "intro_text": "Nuestro catálogo:",
        "button_label": "Ver opciones",
        "sections": [
            {
                "title": "Velas",
                "rows": [
                    {"id": "vela-uno", "title": "Vela Uno", "description": "$1", "product_retailer_id": "prod_1"},
                    {"id": "vela-dos", "title": "Vela Dos", "description": "$2", "product_retailer_id": "prod_2"},
                ],
            }
        ],
    }


async def _dispatch(fake):
    return await _dispatch_intent(
        wa_client=fake,
        wa_dtos=wa_dtos,
        kind="products_list",
        params=_params(),
        fallback={"prefer_native_product_list": True},
        phone_number_id="PH",
        to_number="573001112233",
        last_inbound_message_id=None,
    )


@pytest.mark.asyncio
async def test_falls_back_to_list_when_native_product_list_fails(monkeypatch):
    monkeypatch.setenv("META_CATALOG_ID", "CAT123")
    fake = _FakeWaClient(product_list_ok=False, list_ok=True)
    res = await _dispatch(fake)
    assert fake.product_list_called, "debe intentar el native primero"
    assert fake.list_called, "debe caer al interactive.list cuando el native falla"
    assert res is not None and res.ok


@pytest.mark.asyncio
async def test_uses_native_and_does_not_double_send_when_it_succeeds(monkeypatch):
    monkeypatch.setenv("META_CATALOG_ID", "CAT123")
    fake = _FakeWaClient(product_list_ok=True)
    res = await _dispatch(fake)
    assert fake.product_list_called
    assert not fake.list_called, "si el native anda, NO debe mandar también la lista"
    assert res.ok


# ── La guía de los dos botones de la ficha (2026-09-30) ─────────────────────
# Los botones de la ficha no se pueden cambiar (son de WhatsApp). El texto del
# catálogo sí es nuestro: le dice al cliente cómo pedir una vela y cómo
# preguntar por una (ese botón manda el producto exacto: `referred_product`).


class _Capturing(_FakeWaClient):
    def __init__(self) -> None:
        super().__init__(product_list_ok=True)
        self.outbound = None

    async def send_product_list(self, phone_number_id, to_number, outbound, **k):
        self.outbound = outbound
        return await super().send_product_list()


@pytest.mark.asyncio
async def test_the_catalog_tells_how_to_order_and_how_to_ask_about_one(monkeypatch):
    monkeypatch.setenv("META_CATALOG_ID", "CAT123")
    fake = _Capturing()

    await _dispatch(fake)

    body = fake.outbound.body
    assert body.startswith("Nuestro catálogo:")
    assert "«Añadir a la solicitud de pedido»" in body and "«Enviar mensaje a la empresa»" in body


@pytest.mark.asyncio
async def test_the_list_fallback_has_no_card_buttons_to_explain(monkeypatch):
    """La lista de texto (sin el catálogo de Meta) no tiene esos botones."""
    monkeypatch.setenv("META_CATALOG_ID", "CAT123")
    captured = {}

    class _ListOnly(_FakeWaClient):
        async def send_interactive_list(self, phone_number_id, to_number, outbound, **k):
            captured["body"] = outbound.body
            return await super().send_interactive_list()

    await _dispatch(_ListOnly(product_list_ok=False))

    assert "Enviar mensaje a la empresa" not in captured["body"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("page", "guided"), [(None, True), (1, True), (2, False), ("raro", False)])
async def test_only_the_first_page_carries_the_guide_and_a_strange_page_never_breaks_the_send(
    monkeypatch, page, guided
):
    monkeypatch.setenv("META_CATALOG_ID", "CAT123")
    fake = _Capturing()
    params = {**_params(), "page": page}

    await _dispatch_intent(
        wa_client=fake, wa_dtos=wa_dtos, kind="products_list", params=params,
        fallback={"prefer_native_product_list": True}, phone_number_id="PH",
        to_number="573001112233", last_inbound_message_id=None,
    )

    assert ("Enviar mensaje a la empresa" in fake.outbound.body) is guided
