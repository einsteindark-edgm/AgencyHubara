"""La confirmación y el registro respetan las líneas de un producto repartido.

Laboratorio, caso 4567 (turno 23, producción y los dos bots): con «una lila y
otra azul» en el borrador como `cantidad=2`, `color=Lila` y la otra en
`notas`, la tarjeta salió con `{velon-gorrion, Lila, Lavanda, quantity 2}`: el
cliente confirmó dos lilas. Ahora el borrador guarda una línea por variante
(`set_order_slot(lineas=...)`) y:
  * la confirmación y el registro completan el color y el aroma de cada línea
    con las del borrador cuando el bot no los manda;
  * si el bot junta las líneas (otra cantidad por variante), no sale nada y
    se le dice cuáles son las líneas;
  * la tarjeta dice la variante de cada línea cuando el producto se repite
    (si no, sale igual que siempre).
"""
from __future__ import annotations

from tests.plugins.chats.sales.confirmation_fixture import CONFIRMED_NOW

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO, ProductNotFoundError
from src.platform.orders.port import OrderRegistrationResult
from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool
from src.plugins.chats.agent.sales.tools.ui_intents import PresentOrderConfirmationTool

KEY = "wa_test_variant_lines"
GORRION = "Velón Gorrión"


def _product(handle: str, title: str, price: str, tags: list[str]) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published", tags=tags,
        variants=[CatalogVariantDTO(id=f"variant_{handle}", title="Unico", sku=handle.upper(),
                                    prices=[CatalogPriceDTO(amount=price, currency_code="cop")])],
    )


class _Catalog:
    products = {
        "velon-gorrion": _product(
            "velon-gorrion", GORRION, "44000",
            ["Color: Lila", "Color: Azul", "Aroma: Lavanda", "Aroma: Limoncillo"],
        ),
        "cubo-love": _product("cubo-love", "Cubo Love", "21000", ["Color: Rosado", "Aroma: Café"]),
    }

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        try:
            return self.products[handle]
        except KeyError:
            raise ProductNotFoundError(handle) from None


_LILA = {"producto": GORRION, "aroma": "Lavanda", "color": "Lila", "cantidad": "1"}
_AZUL = {"producto": GORRION, "aroma": "Lavanda", "color": "Azul", "cantidad": "1"}


def _seed(vault: Path, items: list[dict[str, Any]]) -> Path:
    episode = {
        "episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None,
        "order_draft": {"items": items, "slots": {"producto": GORRION}},
    }
    path = vault / KEY / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"episodes": [episode], **CONFIRMED_NOW}, ensure_ascii=False), encoding="utf-8")
    return path


def _ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _line(qty: int, **attrs: Any) -> dict[str, Any]:
    return {"handle": "velon-gorrion", "quantity": qty, "unit_price_cop": 44000, **attrs}


async def _confirm(vault: Path, items: list[dict[str, Any]]) -> dict[str, Any]:
    tool = PresentOrderConfirmationTool(workspace=str(vault), catalog=_Catalog())
    return json.loads(
        await tool.execute_with_context(
            _ctx(), items=items, shipping_cop=7900,
            shipping_address_summary="Calle 10 # 5-20, Chía", payment_method="cash_on_delivery",
        )
    )


def _queued(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")).get("pending_ui_intents") or []


# --- Confirmación ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_lilas_for_one_lila_and_one_azul_is_not_sent(_isolate_vault_dir) -> None:
    """La llamada exacta del turno 23 sobre un borrador ya repartido."""
    path = _seed(_isolate_vault_dir, [_LILA, _AZUL])

    env = await _confirm(_isolate_vault_dir, [_line(2, color="Lila", aroma="Lavanda")])

    assert (env["queued"], env["error"]) == (False, "split_lines_mismatch")
    assert "1× Lila · Lavanda" in env["message"] and "1× Azul · Lavanda" in env["message"]
    assert "una línea por cada una" in env["message"]
    assert _queued(path) == []


@pytest.mark.asyncio
async def test_lines_without_variant_take_the_draft_lines(_isolate_vault_dir) -> None:
    path = _seed(_isolate_vault_dir, [_LILA, _AZUL])

    env = await _confirm(_isolate_vault_dir, [_line(1), _line(1)])

    assert env["queued"] is True, env
    (intent,) = _queued(path)
    assert [it.get("variant") for it in intent["params"]["items"]] == ["Lila · Lavanda", "Azul · Lavanda"]


@pytest.mark.asyncio
async def test_lines_in_another_order_are_the_same_order(_isolate_vault_dir) -> None:
    path = _seed(_isolate_vault_dir, [_LILA, _AZUL])

    env = await _confirm(_isolate_vault_dir, [_line(1, color="azul"), _line(1, color="Lila")])

    assert env["queued"] is True, env
    (intent,) = _queued(path)
    assert [it.get("variant") for it in intent["params"]["items"]] == ["Azul · Lavanda", "Lila · Lavanda"]


@pytest.mark.asyncio
async def test_the_lines_that_say_their_variant_choose_first(_isolate_vault_dir) -> None:
    """Sin color en la primera y «Lila» en la segunda: la segunda se queda con
    la lila y la primera con la azul (no se empareja en el orden en que
    llegan)."""
    path = _seed(_isolate_vault_dir, [_LILA, _AZUL])

    env = await _confirm(_isolate_vault_dir, [_line(1), _line(1, color="Lila")])

    assert env["queued"] is True, env
    (intent,) = _queued(path)
    assert [it.get("variant") for it in intent["params"]["items"]] == ["Azul · Lavanda", "Lila · Lavanda"]


@pytest.mark.asyncio
async def test_an_order_without_split_products_is_sent_as_always(_isolate_vault_dir) -> None:
    path = _seed(_isolate_vault_dir, [{**_LILA, "cantidad": "2"}])

    env = await _confirm(_isolate_vault_dir, [_line(2, color="Lila", aroma="Lavanda")])

    assert env["queued"] is True, env
    (intent,) = _queued(path)
    assert "variant" not in intent["params"]["items"][0]


# --- Tarjeta --------------------------------------------------------------------


async def _card_body(items: list[dict[str, Any]]) -> str:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.platform.whatsapp import dtos as wa_dtos
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import _dispatch_intent

    wa = SimpleNamespace(send_text=AsyncMock(return_value=SimpleNamespace(ok=True)),
                         send_interactive_buttons=AsyncMock(return_value=SimpleNamespace(ok=True)))
    await _dispatch_intent(
        wa_client=wa, wa_dtos=wa_dtos, kind="order_confirmation", fallback={},
        params={"reference_id": "HUB-1", "items": items, "subtotal_cop": 88000, "shipping_cop": 7900,
                "total_cop": 95900, "currency": "COP", "shipping_address_summary": "Calle 1",
                "payment_method": "cash_on_delivery"},
        phone_number_id="phone-1", to_number="573000000000", last_inbound_message_id=None,
    )
    return wa.send_interactive_buttons.await_args.args[2].body


@pytest.mark.asyncio
async def test_the_card_says_the_variant_of_each_line() -> None:
    body = await _card_body([
        {"title": GORRION, "quantity": 1, "unit_price_cop": 44000, "variant": "Lila · Lavanda"},
        {"title": GORRION, "quantity": 1, "unit_price_cop": 44000, "variant": "Azul · Lavanda"},
    ])

    assert "• 1× Velón Gorrión (Lila · Lavanda) — $44.000" in body
    assert "• 1× Velón Gorrión (Azul · Lavanda) — $44.000" in body


@pytest.mark.asyncio
async def test_a_line_without_variant_reads_as_always() -> None:
    body = await _card_body([{"title": GORRION, "quantity": 2, "unit_price_cop": 44000}])

    assert "• 2× Velón Gorrión — $44.000" in body


# --- Registro -------------------------------------------------------------------

_SHIPPING = {"city": "Chía", "neighborhood": "Centro", "address": "Calle 10 # 5-20 casa 3",
             "phone": "3000000000", "receiver_name": "Laura Gómez"}


@dataclass
class _Port:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def register_order(self, **kwargs: Any) -> OrderRegistrationResult:
        self.calls.append(kwargs)
        return OrderRegistrationResult(success=True, order_id="draft_1", provider="medusa",
                                       raw_payload={"id": "draft_1", "display_id": 47})


async def _register(vault: Path, port: _Port, items: list[dict[str, Any]]) -> dict[str, Any]:
    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault, port=port, catalog=_Catalog())
    subtotal = sum(i["unit_price_cop"] * i["quantity"] for i in items)
    return json.loads(await tool.execute_with_context(
        _ctx(), items=items, shipping=_SHIPPING, payment_method="cash_on_delivery",
        subtotal_cop=subtotal, shipping_cop=7900, total_cop=subtotal + 7900,
    ))


@pytest.mark.asyncio
async def test_registering_two_lilas_for_one_lila_and_one_azul_is_refused(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir, [_LILA, _AZUL])
    port = _Port()

    env = await _register(_isolate_vault_dir, port, [_line(2, color="Lila", aroma="Lavanda")])

    assert (env["registered"], env["error"]) == (False, "split_lines_mismatch")
    assert "1× Azul · Lavanda" in env["summary"]
    assert port.calls == []


@pytest.mark.asyncio
async def test_registered_lines_carry_the_variant_of_the_draft(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir, [_LILA, _AZUL])
    port = _Port()

    env = await _register(_isolate_vault_dir, port, [_line(1), _line(1)])

    assert env["registered"] is True, env
    (call,) = port.calls
    assert [(i.quantity, i.color, i.aroma) for i in call["items"]] == [(1, "Lila", "Lavanda"), (1, "Azul", "Lavanda")]
