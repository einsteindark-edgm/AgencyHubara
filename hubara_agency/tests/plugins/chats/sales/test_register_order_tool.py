"""Tests para `RegisterOrderTool` (sesión c4e3416f).

La tool es el cierre formal de la venta — el LLM la llama después de que
el cliente confirma con `present_order_confirmation`. Por ahora es un stub
que persiste a `metadata.registered_order`; futuro = ERP/Medusa Orders.

Garantías:
  1. Persiste el pedido completo a metadata.json.
  2. Devuelve un `order_id` único, no determinístico entre llamadas.
  3. Historial de intentos en `registered_orders_history` (idempotency).
  4. El summary del response instruye al LLM a llamar
     `manage_conversation_tag(COMPRA_EXITOSA)` siguiente.
"""
from __future__ import annotations

import json

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.order_registration import (
    RegisterOrderTool,
)


@pytest.fixture
def ctx():
    return ToolContext(
        session_key="wa_test_register",
        channel="whatsapp",
        chat_id="wa_test_register",
    )


@pytest.fixture
def vault(tmp_path, ctx):
    (tmp_path / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (tmp_path / ctx.session_key / "metadata.json").write_text(
        "{}", encoding="utf-8"
    )
    return tmp_path


def _read_metadata(vault, session_key: str) -> dict:
    return json.loads(
        (vault / session_key / "metadata.json").read_text(encoding="utf-8")
    )


_SAMPLE_ITEMS = [
    {
        "handle": "cruz-de-vida",
        "quantity": 1,
        "unit_price_cop": 17000,
        "variant_label": "Lavanda",
    }
]

_SAMPLE_SHIPPING = {
    "city": "Bogotá",
    "neighborhood": "Chapinero",
    "address": "Calle 100 #15-20 Apto 502",
    "phone": "3001234567",
}


@pytest.mark.asyncio
async def test_register_order_persists_to_metadata(ctx, vault):
    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault)
    result = json.loads(
        await tool.execute_with_context(
            ctx,
            items=_SAMPLE_ITEMS,
            shipping=_SAMPLE_SHIPPING,
            payment_method="transfer",
            subtotal_cop=17000,
            shipping_cop=0,
            total_cop=17000,
        )
    )
    assert result["registered"] is True
    assert result["order_id"].startswith(f"HUB-{ctx.session_key}-")

    metadata = _read_metadata(vault, ctx.session_key)
    order = metadata["registered_order"]
    assert order["items"] == _SAMPLE_ITEMS
    assert order["shipping"] == _SAMPLE_SHIPPING
    assert order["payment_method"] == "transfer"
    assert order["total_cop"] == 17000
    assert order["currency"] == "COP"
    assert order["registered_via"] == "stub_v1"
    assert isinstance(order["registered_at_ms"], int)


@pytest.mark.asyncio
async def test_register_order_idempotent_history(ctx, vault):
    """Si el LLM llama 2 veces (caso raro), el último gana pero el historial
    queda para auditoría."""
    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault)
    r1 = json.loads(
        await tool.execute_with_context(
            ctx,
            items=_SAMPLE_ITEMS,
            shipping=_SAMPLE_SHIPPING,
            payment_method="card",
            subtotal_cop=17000,
            shipping_cop=0,
            total_cop=17000,
        )
    )
    r2 = json.loads(
        await tool.execute_with_context(
            ctx,
            items=_SAMPLE_ITEMS,
            shipping=_SAMPLE_SHIPPING,
            payment_method="cash_on_delivery",
            subtotal_cop=17000,
            shipping_cop=5000,
            total_cop=22000,
        )
    )
    assert r1["order_id"] != r2["order_id"]

    metadata = _read_metadata(vault, ctx.session_key)
    # registered_order = el último
    assert metadata["registered_order"]["order_id"] == r2["order_id"]
    assert metadata["registered_order"]["payment_method"] == "cash_on_delivery"
    # historial preserva ambos
    assert len(metadata["registered_orders_history"]) == 2
    ids_in_history = [h["order_id"] for h in metadata["registered_orders_history"]]
    assert r1["order_id"] in ids_in_history
    assert r2["order_id"] in ids_in_history


@pytest.mark.asyncio
async def test_register_order_summary_directs_llm_to_next_step(ctx, vault):
    """El summary debe decirle al LLM qué hacer después (tag COMPRA_EXITOSA
    + despedida) para que la secuencia canónica de cierre se respete."""
    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault)
    result = json.loads(
        await tool.execute_with_context(
            ctx,
            items=_SAMPLE_ITEMS,
            shipping=_SAMPLE_SHIPPING,
            payment_method="transfer",
            subtotal_cop=17000,
            shipping_cop=0,
            total_cop=17000,
        )
    )
    summary = result["summary"]
    assert "COMPRA_EXITOSA" in summary
    assert "manage_conversation_tag" in summary


@pytest.mark.asyncio
async def test_register_order_handles_corrupted_metadata_gracefully(
    ctx, vault
):
    """Si metadata.json está corrupto, NO debe perder el pedido —
    sobrescribe con el order registrado."""
    (vault / ctx.session_key / "metadata.json").write_text(
        "{ corrupt no comma }", encoding="utf-8"
    )
    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault)
    result = json.loads(
        await tool.execute_with_context(
            ctx,
            items=_SAMPLE_ITEMS,
            shipping=_SAMPLE_SHIPPING,
            payment_method="card",
            subtotal_cop=17000,
            shipping_cop=0,
            total_cop=17000,
        )
    )
    assert result["registered"] is True
    metadata = _read_metadata(vault, ctx.session_key)
    assert metadata["registered_order"]["order_id"] == result["order_id"]
