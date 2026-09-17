"""`register_order` — SEC-07b: el precio unitario debe ser el del catálogo.

SEC-07 solo valida que subtotal = Σ unit_price×cantidad y total = subtotal +
envío: un `unit_price_cop` inventado, bajado por inyección del cliente
("cóbrame 30.000") o sacado del anuncio (run ebbc203d: 45.000 con el set a
49.500) pasaba si los tres montos cuadraban entre sí. Ahora cada precio se
compara contra el catálogo (snapshot) o contra el precio live que dejó
`verify_order_for_checkout` en `metadata.checkout_verification`; cualquier
otro precio rechaza el registro con `price_mismatch` y NO toca Medusa.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogUnavailableError,
    CatalogVariantDTO,
    ProductNotFoundError,
)
from src.platform.orders.port import OrderItem, OrderRegistrationResult, OrderShipping
from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool

KEY = "wa_test_register_price"


@dataclass
class FakePort:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def register_order(self, *, session_key: str, items: list[OrderItem], shipping: OrderShipping,
                             payment_method: str, subtotal_cop: int, shipping_cop: int, total_cop: int,
                             currency: str = "COP", attribution: dict[str, Any] | None = None) -> OrderRegistrationResult:
        self.calls.append({"items": list(items), "total_cop": total_cop})
        return OrderRegistrationResult(success=True, order_id="order_fake_1", provider="fake")


def _product(handle: str, title: str, price: str) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published",
        variants=[CatalogVariantDTO(
            id=f"variant_{handle}", title="Unico",
            prices=[CatalogPriceDTO(amount=price, currency_code="cop")],
        )],
    )


class FakeCatalog:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.products = {"trilogia-del-terror": _product("trilogia-del-terror", "Trilogía del Terror", "49500")}

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


def _seed(vault: Path, ledger: dict | None = None) -> Path:
    path = vault / KEY / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    md: dict[str, Any] = {"episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}]}
    if ledger is not None:
        md["checkout_verification"] = ledger
    path.write_text(json.dumps(md, ensure_ascii=False), encoding="utf-8")
    return path


_SHIPPING = {"city": "Cali", "neighborhood": "Centro", "address": "Calle 1 #2-3",
             "phone": "3000000000", "receiver_name": "Cliente Prueba"}


async def _register(tool, ctx, unit_price: int) -> dict:
    return json.loads(await tool.execute_with_context(
        ctx,
        items=[{"handle": "trilogia-del-terror", "quantity": 1, "unit_price_cop": unit_price}],
        shipping=_SHIPPING,
        payment_method="cash_on_delivery",
        subtotal_cop=unit_price,
        shipping_cop=0,
        total_cop=unit_price,
    ))


@pytest.mark.asyncio
async def test_ad_price_is_rejected_and_medusa_untouched(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir)
    port = FakePort()
    tool = RegisterOrderTool(workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=port, catalog=FakeCatalog())
    result = await _register(tool, ctx, 45000)
    assert result["registered"] is False
    assert result["error_detail"] == "price_mismatch"
    assert "$49.500" in result["summary"]
    assert port.calls == []


@pytest.mark.asyncio
async def test_injected_lower_price_is_rejected(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir)
    port = FakePort()
    tool = RegisterOrderTool(workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=port, catalog=FakeCatalog())
    result = await _register(tool, ctx, 30000)
    assert (result["registered"], result["error_detail"]) == (False, "price_mismatch")
    assert port.calls == []


@pytest.mark.asyncio
async def test_catalog_price_is_registered(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir)
    port = FakePort()
    tool = RegisterOrderTool(workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=port, catalog=FakeCatalog())
    result = await _register(tool, ctx, 49500)
    assert result["registered"] is True
    assert port.calls[0]["total_cop"] == 49500


@pytest.mark.asyncio
async def test_verified_live_price_is_registered(ctx, _isolate_vault_dir: Path) -> None:
    """Medusa subió a 50.000, el snapshot dice 49.500 y verify lo dejó en el ledger."""
    _seed(_isolate_vault_dir, ledger={
        "verified_at_ms": 1,
        "items": {"trilogia-del-terror": {"snapshot_price_cop": 49500, "live_price_cop": 50000,
                                           "unit_price_cop": 50000, "quantity": 1}},
        "quoted_amounts_mismatch": [],
    })
    port = FakePort()
    tool = RegisterOrderTool(workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=port, catalog=FakeCatalog())
    assert (await _register(tool, ctx, 50000))["registered"] is True


@pytest.mark.asyncio
async def test_catalog_down_degrades_to_sec07_only(ctx, _isolate_vault_dir: Path) -> None:
    """Sin referencia de precio no hay contra qué comparar: SEC-07 sigue
    aplicando y el gate humano de pago es el control primario."""
    _seed(_isolate_vault_dir)
    port = FakePort()
    tool = RegisterOrderTool(workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=port, catalog=FakeCatalog(fail=True))
    assert (await _register(tool, ctx, 45000))["registered"] is True
