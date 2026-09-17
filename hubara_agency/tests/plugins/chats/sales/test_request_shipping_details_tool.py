"""Tests para `RequestShippingDetailsTool` — el intent encolado debe
traer `payment_options` dinámicas (2 ó 3 según el total de productos).

Por qué importa: el Flow JSON de Meta (single-screen, ver
`hubara_agency/docs/whatsapp_flows/shipping_v2.json`) bindea
`data-source: ${data.payment_options}` en el RadioButtonsGroup de método
de pago. El operador NO necesita re-editar y re-publicar el Flow para
cambiar las opciones de pago — esta lista, construida acá, se manda en
`flow_action_data` y Meta la renderiza tal cual.

Política Hubara: contra entrega desde $45.000 COP en productos (margen vs
costo del envío; umbral INCLUSIVO en `config/shipping.py`). Tests cubren los
3 thresholds canónicos (under, at boundary, over) + shape del payload.

Desde el incidente run ebbc203d (2026-09-16) la tool recibe `items`
(handle + cantidad) y el total sale del CATÁLOGO — el LLM no manda montos.
"""
from __future__ import annotations

import json

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    ProductNotFoundError,
)
from src.plugins.chats.agent.sales.tools.ui_intents import (
    RequestShippingDetailsTool,
)


def _product(handle: str, title: str, price: int) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published",
        variants=[CatalogVariantDTO(
            id=f"variant_{handle}", title="Unico",
            prices=[CatalogPriceDTO(amount=str(price), currency_code="cop")],
        )],
    )


class _Catalog:
    products = {
        "vela-cruz-de-vida": _product("vela-cruz-de-vida", "Vela Cruz de Vida", 17000),
        "velas-pack": _product("velas-pack", "Velas pack", 15000),
        "velas-grandes": _product("velas-grandes", "Velas grandes", 45000),
        "velas": _product("velas", "Velas", 25000),
        "vela": _product("vela", "Vela", 20000),
    }

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        try:
            return self.products[handle]
        except KeyError:
            raise ProductNotFoundError(handle) from None


def _items(handle: str, quantity: int = 1) -> list[dict]:
    return [{"handle": handle, "quantity": quantity}]


@pytest.fixture
def ctx():
    return ToolContext(
        session_key="wa_test_shipping",
        channel="whatsapp",
        chat_id="wa_test_shipping",
    )


@pytest.fixture
def seeded_vault(tmp_path, ctx):
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    # Guarda 2026-09-14: el formulario exige confirmación de compra en el
    # episodio activo (el cliente dijo que sí a un producto).
    (vault / ctx.session_key / "metadata.json").write_text(
        json.dumps({
            "episodes": [{
                "episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None,
                "order_draft": {"slots": {"producto": "Cubo Love"}, "updated_at_ms": 1,
                                "confirmed_at_ms": 2, "confirmed_by": "text"},
            }],
        }),
        encoding="utf-8",
    )
    return vault


@pytest.fixture
def tool(seeded_vault):
    return RequestShippingDetailsTool(workspace=str(seeded_vault), catalog=_Catalog())


def _read_intent(vault, session_key: str) -> dict:
    data = json.loads(
        (vault / session_key / "metadata.json").read_text(encoding="utf-8")
    )
    intents = data.get("pending_ui_intents") or []
    assert len(intents) == 1, f"esperaba 1 intent, got {len(intents)}"
    return intents[0]


@pytest.mark.asyncio
async def test_payment_options_excludes_cod_below_45k(ctx, seeded_vault, tool):
    """Pedido chico ($17.000) → solo pago anticipado + link de pago, sin
    contra entrega (política Hubara: COD desde $45.000 para asegurar
    margen). Requisito 2026-08-31: 'tarjeta' ya NO es una opción — los
    pagos con tarjeta van por el link de pago (con recargo)."""
    result = json.loads(await tool.execute_with_context(ctx, items=_items("vela-cruz-de-vida")))
    assert result["queued"] is True

    intent = _read_intent(seeded_vault, ctx.session_key)
    assert intent["kind"] == "shipping_flow"

    flow_data = intent["params"]["flow_action_data"]
    payment_options = flow_data["payment_options"]
    ids = [opt["id"] for opt in payment_options]
    assert ids == ["transfer", "payment_link"]
    assert flow_data["show_cash_on_delivery"] is False


@pytest.mark.asyncio
async def test_payment_options_includes_cod_at_45k_boundary(ctx, seeded_vault, tool):
    """Incidente run ebbc203d (2026-09-16): el guion dice "contra entrega
    desde $45.000 en productos" y el bot se lo afirmó al cliente, pero el
    código usaba `> 45000` estricto → el formulario ocultó la opción y la
    clienta abandonó el Flow. La política es INCLUSIVA: $45.000 exacto
    ofrece contra entrega (una sola fuente: `config/shipping.py`)."""
    await tool.execute_with_context(ctx, items=_items("velas-pack", 3))  # 3 × 15.000

    intent = _read_intent(seeded_vault, ctx.session_key)
    flow_data = intent["params"]["flow_action_data"]
    assert flow_data["order_total_cop"] == 45000
    ids = [opt["id"] for opt in flow_data["payment_options"]]
    assert ids == ["cash_on_delivery", "transfer", "payment_link"]
    assert flow_data["show_cash_on_delivery"] is True


@pytest.mark.asyncio
async def test_payment_options_includes_cod_over_45k(ctx, seeded_vault, tool):
    """Pedido grande ($90.000) → 3 opciones con contra entrega PRIMERA
    (orden del requisito 2026-08-31), flag `show_cash_on_delivery` en true
    para data binding adicional del Flow si fuera necesario."""
    await tool.execute_with_context(ctx, items=_items("velas-grandes", 2))  # 2 × 45.000

    intent = _read_intent(seeded_vault, ctx.session_key)
    flow_data = intent["params"]["flow_action_data"]
    ids = [opt["id"] for opt in flow_data["payment_options"]]
    assert ids == ["cash_on_delivery", "transfer", "payment_link"]
    assert flow_data["show_cash_on_delivery"] is True

    # Cada opción trae title amigable con emoji
    titles = {opt["id"]: opt["title"] for opt in flow_data["payment_options"]}
    assert "Contra entrega" in titles["cash_on_delivery"]
    assert "Pago anticipado" in titles["transfer"]
    assert "Link de pago" in titles["payment_link"]


@pytest.mark.asyncio
async def test_payment_options_descriptions_inform_terms(ctx, seeded_vault, tool):
    """Requisito 2026-08-31 — cada forma de pago se informa con su condición:
    contra entrega → el valor lo calcula la transportadora; anticipado →
    Nequi o llave 3229041190; link de pago → recargo 1,5% (Nequi/
    Bancolombia) o 2,69% (otros bancos)."""
    await tool.execute_with_context(ctx, items=_items("velas-grandes", 2))

    intent = _read_intent(seeded_vault, ctx.session_key)
    options = intent["params"]["flow_action_data"]["payment_options"]
    desc = {opt["id"]: opt["description"] for opt in options}
    assert "transportadora" in desc["cash_on_delivery"].lower()
    assert "Nequi" in desc["transfer"]
    assert "3229041190" in desc["transfer"]
    assert "llave" in desc["transfer"].lower()
    assert "1,5%" in desc["payment_link"]
    assert "2,69%" in desc["payment_link"]


@pytest.mark.asyncio
async def test_intent_shape_for_meta_flow_compat(ctx, seeded_vault, tool):
    """El intent debe traer EXACTAMENTE los campos que espera el Flow JSON
    de Meta (single-screen `SHIPPING_DETAILS`). Anti-regresión: si alguien
    cambia el nombre de un campo (ej. `items_summary` → `summary`) sin
    actualizar el JSON publicado en Meta, el Flow se rompe en runtime
    (renderiza variables vacías). Esta firma debe quedar estable."""
    await tool.execute_with_context(ctx, items=_items("velas", 2))  # 2 × 25.000

    intent = _read_intent(seeded_vault, ctx.session_key)
    params = intent["params"]

    # Shape canónico para `wa_dtos.InteractiveFlowOutbound`
    assert params["flow_action"] == "navigate"
    assert params["flow_action_screen"] == "SHIPPING_DETAILS"
    assert params["flow_cta"] == "Completar datos"
    # Placeholder a propósito — el dispatcher lo resuelve desde env productivo
    assert params["flow_id"] == "FLOW_ID_SHIPPING_PLACEHOLDER"
    # `flow_token` único por sesión
    assert params["flow_token"].startswith("shipping_wa_test_shipping_")

    flow_data = params["flow_action_data"]
    # Las 4 keys que el JSON v1 espera en `data:`
    assert set(flow_data.keys()) == {
        "order_total_cop",
        "items_summary",
        "show_cash_on_delivery",
        "payment_options",
    }
    assert flow_data["order_total_cop"] == 50000
    assert flow_data["items_summary"] == "2× Velas"

    # `order_total_cop` también en params (para el fallback texto plano)
    assert params["order_total_cop"] == 50000


@pytest.mark.asyncio
async def test_summary_instructs_llm_to_wait_not_repeat(ctx, seeded_vault, tool):
    """El summary que devuelve la tool al LLM debe dejar claro que NO pida
    los mismos datos otra vez (anti-eco) y que espere la respuesta del
    cliente. Este wording llega al prompt del LLM como tool_result."""
    result = json.loads(await tool.execute_with_context(ctx, items=_items("vela")))
    summary = result["summary"]
    # El LLM debe saber que vendrá la respuesta vía texto o nfm_reply
    assert "verify_order_for_checkout" in summary
    # Y NO re-pedir los datos
    assert "NO" in summary or "no" in summary
