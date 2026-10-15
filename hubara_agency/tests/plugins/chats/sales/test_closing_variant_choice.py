"""El resumen y el registro no salen sin la opción de Medusa escogida
(2026-10-14, ···0686).

Las Velas de deseos tienen la opción «Color» (Rojo y verde, Multicolor). El
bot armó el pedido sin que la clienta escogiera: `present_order_confirmation`
mostró «2× Velas de deseos cuadradas» y `register_order` mandó
`variant_label: "Navidad"` (el aroma). Medusa cayó a la primera variante y un
colega tuvo que preguntarle el color. Es un hecho del catálogo: un producto
con una opción de dos valores o más no se confirma ni se registra sin uno de
ellos (en la línea —color, diseño, aroma o `variant_label`— o en el
borrador). El texto vuelve al modelo con lo que falta.

No frena (gotcha 16): el producto de una sola variante, la opción escogida en
la línea o en el borrador, sin catálogo, ni al operador que registra desde el
panel (`closing_guard=False`: ya habló con el cliente).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO, ProductNotFoundError
from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool
from src.plugins.chats.agent.sales.tools.ui_intents import PresentOrderConfirmationTool
from tests.plugins.chats.sales.test_register_order_tool import _SAMPLE_SHIPPING, FakeOrderRegistrationPort

KEY = "wa_test_variant_choice"
HANDLE = "velas-de-deseos-cuadradas"


def _variant(title: str) -> CatalogVariantDTO:
    return CatalogVariantDTO(
        id=f"variant_{title}", title=title, sku=f"HUB-{title}",
        prices=[CatalogPriceDTO(amount="25000", currency_code="cop")],
    )


DESEOS = CatalogProductDTO(
    id="prod_deseos", handle=HANDLE, title="Velas de deseos cuadradas", status="published",
    tags=["Aroma: Navidad"], options={"Color": ["Rojo y verde", "Multicolor"]},
    variants=[_variant("Multicolor"), _variant("Rojo y verde")],
)
TRILOGIA = CatalogProductDTO(
    id="prod_trilogia", handle="trilogia-del-terror", title="Trilogía del Terror", status="published",
    tags=["Aroma: Frutos rojos", "Color: Blanco"], options={"Unico": ["Unico"]}, variants=[_variant("Unico")],
)


class _Catalog:
    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        products = {p.handle: p for p in (DESEOS, TRILOGIA)}
        try:
            return products[handle]
        except KeyError:
            raise ProductNotFoundError(handle) from None


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _seed(vault: Path, item: dict | None = None) -> None:
    draft = {
        "items": [item or {"producto": "Velas de deseos cuadradas", "cantidad": "2", "aroma": "Navidad"}],
        "confirmed_at_ms": 5, "confirmed_by": "text",
    }
    md = {"episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None, "order_draft": draft}],
          "last_inbound_message_id": "wamid.ULTIMO"}
    path = vault / KEY / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(md, ensure_ascii=False), encoding="utf-8")


async def _present(vault: Path, ctx: ToolContext, **line) -> dict:
    tool = PresentOrderConfirmationTool(workspace=str(vault), catalog=_Catalog())
    item = {"handle": HANDLE, "quantity": 2, "unit_price_cop": 25000, **line}
    return json.loads(await tool.execute_with_context(
        ctx, items=[item], shipping_cop=16940, shipping_address_summary="Medellín",
        payment_method="cash_on_delivery",
    ))


async def _register(vault: Path, ctx: ToolContext, *, closing_guard: bool = True, **line) -> tuple[dict, list]:
    port = FakeOrderRegistrationPort()
    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault, port=port, catalog=_Catalog(),
                             closing_guard=closing_guard)
    item = {"handle": HANDLE, "quantity": 2, "unit_price_cop": 25000, **line}
    result = json.loads(await tool.execute_with_context(
        ctx, items=[item], shipping=_SAMPLE_SHIPPING, payment_method="cash_on_delivery",
        subtotal_cop=50000, shipping_cop=7900, total_cop=57900,  # la muestra es de Bogotá
    ))
    return result, port.calls


@pytest.mark.asyncio
async def test_the_incident_summary_does_not_go_without_the_color(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir)

    result = await _present(_isolate_vault_dir, ctx, aroma="Navidad")

    assert (result["queued"], result["error"]) == (False, "variant_not_chosen")
    assert "Rojo y verde" in result["message"] and "Multicolor" in result["message"]
    assert "present_variant_picker" in result["message"]


@pytest.mark.asyncio
async def test_the_incident_order_is_not_registered_with_the_aroma_as_variant(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir)

    result, calls = await _register(_isolate_vault_dir, ctx, aroma="Navidad", variant_label="Navidad")

    assert (result["registered"], result["error_detail"]) == (False, "variant_not_chosen")
    assert "Rojo y verde" in result["summary"] and calls == []


@pytest.mark.parametrize("line", [{"color": "Multicolor"}, {"variant_label": "Rojo y verde"}])
@pytest.mark.asyncio
async def test_the_color_in_the_line_lets_it_through(ctx, _isolate_vault_dir: Path, line: dict) -> None:
    _seed(_isolate_vault_dir)

    result, calls = await _register(_isolate_vault_dir, ctx, **line)

    assert result["registered"] is True and len(calls) == 1


@pytest.mark.asyncio
async def test_the_color_in_the_draft_lets_the_summary_through(ctx, _isolate_vault_dir: Path) -> None:
    """Pedido 78: el modelo anotó la opción como `diseno` en el borrador."""
    _seed(_isolate_vault_dir, {"producto": "Velas de deseos cuadradas", "cantidad": "2", "diseno": "Rojo y verde"})

    result = await _present(_isolate_vault_dir, ctx)

    assert result.get("error") != "variant_not_chosen"


@pytest.mark.asyncio
async def test_a_product_without_options_is_not_stopped(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, {"producto": "Trilogía del Terror", "cantidad": "1"})
    tool = PresentOrderConfirmationTool(workspace=str(_isolate_vault_dir), catalog=_Catalog())

    result = json.loads(await tool.execute_with_context(
        ctx, items=[{"handle": "trilogia-del-terror", "quantity": 1, "unit_price_cop": 25000}],
        shipping_cop=16940, shipping_address_summary="Medellín", payment_method="cash_on_delivery",
    ))

    assert result.get("error") != "variant_not_chosen"


@pytest.mark.asyncio
async def test_the_operator_panel_is_not_stopped(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir)

    result, calls = await _register(_isolate_vault_dir, ctx, closing_guard=False, aroma="Navidad")

    assert result["registered"] is True and len(calls) == 1
