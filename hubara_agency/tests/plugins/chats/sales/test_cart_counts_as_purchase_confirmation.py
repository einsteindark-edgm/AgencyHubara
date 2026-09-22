"""El carrito del catálogo de WhatsApp ES la confirmación de compra.

Incidente (run sales 01a0cb16, 2026-09-22): la clienta armó el carrito
(`[el cliente armó un carrito con: 1× HUB-TRILOGIA]`) y `request_shipping_details`
lo rechazó con `purchase_not_confirmed` — la guarda solo reconocía un "sí"
escrito o el botón "Confirmar". Mandar el carrito es la señal de compra más
explícita que tiene el canal: pedirle "¿lo dejamos así?" encima es un paso de
más (y, con el corte de turno de entonces, el bot se quedó callado).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.tools.ui_intents import RequestShippingDetailsTool
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.plugins.chats.shared.purchase_signals import (
    has_purchase_confirmation,
    register_inbound_purchase_signals,
)
from src.platform.state import FilesystemMetadataStore

SESSION = "wa_573001234567"
NOW = 1_789_406_554_683
_CART = {
    "catalog_id": "868785339159351",
    "product_items": [
        {"product_retailer_id": "HUB-TRILOGIA", "quantity": 1, "item_price": 45000, "currency": "COP"}
    ],
}


class _Catalog:
    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        assert handle == "trilogia-del-terror", handle
        return CatalogProductDTO(
            id="prod_trilogia", handle="trilogia-del-terror", title="Trilogía del Terror",
            status="published",
            variants=[CatalogVariantDTO(
                id="variant_trilogia", title="Unico",
                prices=[CatalogPriceDTO(amount="45000", currency_code="cop")],
            )],
        )


class _FakeHistoryStore:
    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        pass


class _FakeLoadOrStart:
    async def execute(self, *a: Any, **kw: Any) -> None:
        pass


def _cart_message(message_id: str) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=message_id,
        from_number="573001234567",
        phone_number_id="PID",
        text=None,
        media=None,
        timestamp="1714312345",
        order=_CART,
    )


def test_a_cart_confirms_even_before_the_draft_has_a_product() -> None:
    """El carrito nombra el producto por sí mismo: no depende de que el LLM ya
    haya llenado `producto` en el draft (a diferencia de un "sí" suelto)."""
    md: dict[str, Any] = {"episodes": [{"episode_id": "ep_001", "closed_at_ms": None}]}

    kind = register_inbound_purchase_signals(
        md, "[el cliente armó un carrito con: 1× HUB-TRILOGIA]",
        now_ms=NOW, message_id="wamid.cart", order=_CART,
    )

    assert kind == "affirmation"
    assert has_purchase_confirmation(md) is True
    assert md["episodes"][0]["order_draft"]["confirmed_by"] == "cart"


def test_an_empty_cart_is_not_a_confirmation() -> None:
    md: dict[str, Any] = {"episodes": [{"episode_id": "ep_001", "closed_at_ms": None}]}

    register_inbound_purchase_signals(
        md, "[el cliente armó un carrito con: (carrito vacío)]",
        now_ms=NOW, message_id="wamid.cart", order={"product_items": []},
    )

    assert has_purchase_confirmation(md) is False


@pytest.mark.asyncio
async def test_after_a_cart_the_shipping_form_goes_out(_isolate_vault_dir: Path) -> None:
    """Cadena real del run 01a0cb16: carrito → ingest → el LLM pide los datos de
    envío. Antes: `purchase_not_confirmed`. Ahora el formulario sale."""
    store = FilesystemMetadataStore(_isolate_vault_dir)
    ingest = IngestInboundMessage(
        history_store=_FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=_FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=store,
    )
    await ingest.execute(_cart_message("wamid.cart"))

    tool = RequestShippingDetailsTool(workspace=str(_isolate_vault_dir), catalog=_Catalog())
    ctx = ToolContext(session_key=SESSION, channel="whatsapp", chat_id=SESSION)
    result = json.loads(await tool.execute_with_context(
        ctx, items=[{"handle": "trilogia-del-terror", "quantity": 1}]
    ))

    assert result.get("error") is None, result
    assert result["queued"] is True
    assert [i["kind"] for i in store.read(SESSION)["pending_ui_intents"]] == ["shipping_flow"]
