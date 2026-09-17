"""`request_shipping_details` calcula el total desde el CATÁLOGO, nunca desde
el LLM (incidente run ebbc203d, 2026-09-16).

En ese run el anuncio decía "$45.000", el catálogo tenía el set a $49.500 y
el LLM llamó `request_shipping_details(order_total_cop=45000)`: la tool le
devolvió "dile el precio ($45.000)" (eco del parámetro) y, como 45.000 no
superaba el umbral, el formulario ocultó "Contra entrega". Ahora la tool
recibe `items` (handle + cantidad), resuelve precio y título en el catálogo
y deriva total, resumen y opciones de pago de ahí. Un `order_total_cop` del
LLM se ignora (y se loguea).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogUnavailableError,
    CatalogVariantDTO,
    ProductNotFoundError,
)
from src.plugins.chats.agent.sales.tools.ui_intents import RequestShippingDetailsTool

KEY = "wa_test_catalog_price"


def _product(handle: str, title: str, price: str) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published",
        variants=[CatalogVariantDTO(
            id=f"variant_{handle}", title="Unico", sku=f"HUB-{handle.upper()}",
            prices=[CatalogPriceDTO(amount=price, currency_code="cop")],
        )],
    )


class FakeCatalog:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.products = {
            "trilogia-del-terror": _product("trilogia-del-terror", "Trilogía del Terror", "49500"),
            "calabaza": _product("calabaza", "Calabaza", "16000"),
        }

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        if self.fail:
            raise CatalogUnavailableError("snapshot ausente")
        try:
            return self.products[handle]
        except KeyError:
            raise ProductNotFoundError(handle) from None


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _seed(vault: Path, *, confirmed: bool) -> Path:
    draft = {"slots": {"producto": "Trilogía del Terror", "cantidad": "1"}, "updated_at_ms": 1}
    if confirmed:
        draft.update({"confirmed_at_ms": 2, "confirmed_by": "text"})
    path = vault / KEY / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "last_inbound_message_id": "wamid.yes",
        "episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None, "order_draft": draft}],
    }), encoding="utf-8")
    return path


def _intents(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("pending_ui_intents") or []


@pytest.mark.asyncio
async def test_total_and_summary_come_from_catalog(ctx, _isolate_vault_dir: Path) -> None:
    path = _seed(_isolate_vault_dir, confirmed=True)
    tool = RequestShippingDetailsTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog())
    result = json.loads(await tool.execute_with_context(
        ctx, items=[{"handle": "trilogia-del-terror", "quantity": 1}],
    ))
    assert result["queued"] is True
    assert result["order_total_cop"] == 49500
    (intent,) = _intents(path)
    flow = intent["params"]["flow_action_data"]
    assert flow["order_total_cop"] == 49500
    assert flow["items_summary"] == "1× Trilogía del Terror"
    assert intent["params"]["order_total_cop"] == 49500
    # 49.500 ≥ 45.000 → contra entrega disponible
    assert flow["show_cash_on_delivery"] is True
    assert [o["id"] for o in flow["payment_options"]][0] == "cash_on_delivery"


@pytest.mark.asyncio
async def test_llm_total_is_ignored(ctx, _isolate_vault_dir: Path) -> None:
    """El run real: el LLM mandó 45000 (precio del anuncio). Gana el catálogo."""
    path = _seed(_isolate_vault_dir, confirmed=True)
    tool = RequestShippingDetailsTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog())
    result = json.loads(await tool.execute_with_context(
        ctx, items=[{"handle": "trilogia-del-terror", "quantity": 1}],
        order_total_cop=45000, items_summary="1× Trilogía del Terror",
    ))
    assert result["order_total_cop"] == 49500
    (intent,) = _intents(path)
    assert intent["params"]["flow_action_data"]["order_total_cop"] == 49500


@pytest.mark.asyncio
async def test_multi_item_subtotal_and_summary(ctx, _isolate_vault_dir: Path) -> None:
    path = _seed(_isolate_vault_dir, confirmed=True)
    tool = RequestShippingDetailsTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog())
    await tool.execute_with_context(ctx, items=[
        {"handle": "trilogia-del-terror", "quantity": 2},
        {"handle": "calabaza", "quantity": 1},
    ])
    (intent,) = _intents(path)
    flow = intent["params"]["flow_action_data"]
    assert flow["order_total_cop"] == 2 * 49500 + 16000
    assert flow["items_summary"] == "2× Trilogía del Terror, 1× Calabaza"


@pytest.mark.asyncio
async def test_purchase_not_confirmed_quotes_catalog_price(ctx, _isolate_vault_dir: Path) -> None:
    """La guía al LLM ("dile el precio (...)") cita el precio del catálogo,
    no el número que el LLM haya mandado."""
    path = _seed(_isolate_vault_dir, confirmed=False)
    tool = RequestShippingDetailsTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog())
    result = json.loads(await tool.execute_with_context(
        ctx, items=[{"handle": "trilogia-del-terror", "quantity": 1}], order_total_cop=45000,
    ))
    assert result["error"] == "purchase_not_confirmed"
    assert "$49.500" in result["message"]
    assert "45.000" not in result["message"]
    assert _intents(path) == []


@pytest.mark.asyncio
async def test_unknown_handle_is_rejected(ctx, _isolate_vault_dir: Path) -> None:
    path = _seed(_isolate_vault_dir, confirmed=True)
    tool = RequestShippingDetailsTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog())
    result = json.loads(await tool.execute_with_context(
        ctx, items=[{"handle": "vela-inventada", "quantity": 1}],
    ))
    assert result["queued"] is False
    assert result["error"] == "unknown_handle"
    assert "vela-inventada" in result["message"]
    assert _intents(path) == []


@pytest.mark.asyncio
async def test_catalog_unavailable_is_reported(ctx, _isolate_vault_dir: Path) -> None:
    path = _seed(_isolate_vault_dir, confirmed=True)
    tool = RequestShippingDetailsTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog(fail=True))
    result = json.loads(await tool.execute_with_context(
        ctx, items=[{"handle": "trilogia-del-terror", "quantity": 1}],
    ))
    assert result["queued"] is False
    assert result["error"] == "catalog_unavailable"
    assert _intents(path) == []


def test_schema_takes_items_not_a_total() -> None:
    """El LLM ya no puede mandar un monto: el schema pide handle + cantidad."""
    params = RequestShippingDetailsTool.parameters
    assert "items" in params["required"]
    assert "order_total_cop" not in params["properties"]
    assert "items_summary" not in params["properties"]
    item_props = params["properties"]["items"]["items"]["properties"]
    assert set(item_props) == {"handle", "quantity"}
