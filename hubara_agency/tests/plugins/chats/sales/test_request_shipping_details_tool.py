"""Tests para `RequestShippingDetailsTool` — el intent encolado debe
traer `payment_options` dinámicas (2 ó 3 según total).

Por qué importa: el Flow JSON de Meta (single-screen, ver
`hubara_agency/docs/whatsapp_flows/shipping_v1.json`) bindea
`data-source: ${data.payment_options}` en el RadioButtonsGroup de método
de pago. El operador NO necesita re-editar y re-publicar el Flow para
cambiar las opciones de pago — esta lista, construida acá, se manda en
`flow_action_data` y Meta la renderiza tal cual.

Política Hubara: contra entrega solo disponible para pedidos > $45.000 COP
(margen vs costo del envío). Tests cubren los 3 thresholds canónicos
(under, at boundary, over) + shape del payload.
"""
from __future__ import annotations

import json

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.ui_intents import (
    RequestShippingDetailsTool,
)


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
    (vault / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")
    return vault


def _read_intent(vault, session_key: str) -> dict:
    data = json.loads(
        (vault / session_key / "metadata.json").read_text(encoding="utf-8")
    )
    intents = data.get("pending_ui_intents") or []
    assert len(intents) == 1, f"esperaba 1 intent, got {len(intents)}"
    return intents[0]


@pytest.mark.asyncio
async def test_payment_options_excludes_cod_below_45k(ctx, seeded_vault):
    """Pedido chico ($17.000) → solo Tarjeta + Transferencia, sin contra
    entrega (política Hubara: COD solo > $45.000 para asegurar margen)."""
    tool = RequestShippingDetailsTool(workspace=str(seeded_vault))
    result = json.loads(await tool.execute_with_context(
        ctx,
        order_total_cop=17000,
        items_summary="1× Vela Cruz de Vida",
    ))
    assert result["queued"] is True

    intent = _read_intent(seeded_vault, ctx.session_key)
    assert intent["kind"] == "shipping_flow"

    flow_data = intent["params"]["flow_action_data"]
    payment_options = flow_data["payment_options"]
    ids = [opt["id"] for opt in payment_options]
    assert ids == ["card", "transfer"]
    assert flow_data["show_cash_on_delivery"] is False


@pytest.mark.asyncio
async def test_payment_options_excludes_cod_at_45k_boundary(ctx, seeded_vault):
    """El threshold es `> 45000` estricto — $45.000 exacto NO ofrece COD."""
    tool = RequestShippingDetailsTool(workspace=str(seeded_vault))
    await tool.execute_with_context(
        ctx,
        order_total_cop=45000,
        items_summary="3× Velas pack",
    )

    intent = _read_intent(seeded_vault, ctx.session_key)
    flow_data = intent["params"]["flow_action_data"]
    ids = [opt["id"] for opt in flow_data["payment_options"]]
    assert ids == ["card", "transfer"]
    assert flow_data["show_cash_on_delivery"] is False


@pytest.mark.asyncio
async def test_payment_options_includes_cod_over_45k(ctx, seeded_vault):
    """Pedido grande ($90.000) → 3 opciones incluyendo contra entrega,
    flag `show_cash_on_delivery` en true para que el Flow JSON pueda usar
    data binding adicional si fuera necesario."""
    tool = RequestShippingDetailsTool(workspace=str(seeded_vault))
    await tool.execute_with_context(
        ctx,
        order_total_cop=90000,
        items_summary="2× Velas grandes + envío Bogotá",
    )

    intent = _read_intent(seeded_vault, ctx.session_key)
    flow_data = intent["params"]["flow_action_data"]
    ids = [opt["id"] for opt in flow_data["payment_options"]]
    assert ids == ["card", "transfer", "cash_on_delivery"]
    assert flow_data["show_cash_on_delivery"] is True

    # Cada opción trae title amigable con emoji
    titles = {opt["id"]: opt["title"] for opt in flow_data["payment_options"]}
    assert "Tarjeta" in titles["card"]
    assert "Transferencia" in titles["transfer"]
    assert "Contra entrega" in titles["cash_on_delivery"]


@pytest.mark.asyncio
async def test_intent_shape_for_meta_flow_compat(ctx, seeded_vault):
    """El intent debe traer EXACTAMENTE los campos que espera el Flow JSON
    de Meta (single-screen `SHIPPING_DETAILS`). Anti-regresión: si alguien
    cambia el nombre de un campo (ej. `items_summary` → `summary`) sin
    actualizar el JSON publicado en Meta, el Flow se rompe en runtime
    (renderiza variables vacías). Esta firma debe quedar estable."""
    tool = RequestShippingDetailsTool(workspace=str(seeded_vault))
    await tool.execute_with_context(
        ctx,
        order_total_cop=50000,
        items_summary="2× Velas",
    )

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
async def test_summary_instructs_llm_to_wait_not_repeat(ctx, seeded_vault):
    """El summary que devuelve la tool al LLM debe dejar claro que NO pida
    los mismos datos otra vez (anti-eco) y que espere la respuesta del
    cliente. Este wording llega al prompt del LLM como tool_result."""
    tool = RequestShippingDetailsTool(workspace=str(seeded_vault))
    result_str = await tool.execute_with_context(
        ctx,
        order_total_cop=20000,
        items_summary="1× Vela",
    )
    result = json.loads(result_str)
    summary = result["summary"]
    # El LLM debe saber que vendrá la respuesta vía texto o nfm_reply
    assert "verify_order_for_checkout" in summary
    # Y NO re-pedir los datos
    assert "NO" in summary or "no" in summary
