"""`present_order_confirmation` solo acepta precios del catálogo — EXACTOS.

Incidente run ebbc203d (2026-09-16) + defensa contra inyección de precios:
el LLM manda `unit_price_cop` y antes se toleraba un drift del 5% contra el
snapshot. Un precio "bajado" 2% (por error del modelo o porque el cliente
escribió "cóbrame 48.500") pasaba al resumen y a la orden. Ahora cualquier
precio que no sea el del catálogo (snapshot, o el live que verificó
`verify_order_for_checkout` en esta sesión) rechaza el intent con
`price_mismatch` y devuelve los precios correctos.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    ProductNotFoundError,
)
from src.plugins.chats.agent.sales.tools.ui_intents import PresentOrderConfirmationTool

KEY = "wa_test_confirmation_price"


def _product(handle: str, title: str, price: str) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published",
        variants=[CatalogVariantDTO(
            id=f"variant_{handle}", title="Unico", sku=f"HUB-{handle.upper()}",
            prices=[CatalogPriceDTO(amount=price, currency_code="cop")],
        )],
    )


class FakeCatalog:
    products = {
        "trilogia-del-terror": _product("trilogia-del-terror", "Trilogía del Terror", "49500"),
    }

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        try:
            return self.products[handle]
        except KeyError:
            raise ProductNotFoundError(handle) from None


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _seed(vault: Path, extra: dict | None = None) -> Path:
    path = vault / KEY / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    md = {"episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}]}
    md.update(extra or {})
    path.write_text(json.dumps(md, ensure_ascii=False), encoding="utf-8")
    return path


def _intents(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("pending_ui_intents") or []


async def _present(tool, ctx, unit_price: int) -> dict:
    return json.loads(await tool.execute_with_context(
        ctx,
        items=[{"handle": "trilogia-del-terror", "quantity": 1, "unit_price_cop": unit_price}],
        shipping_cop=0,
        shipping_address_summary="Calle 1 #2-3, Centro, Cali",
        payment_method="cash_on_delivery",
    ))


@pytest.mark.asyncio
async def test_small_deviation_is_rejected(ctx, _isolate_vault_dir: Path) -> None:
    """48.500 vs 49.500 (2%): antes pasaba por la tolerancia del 5%."""
    path = _seed(_isolate_vault_dir)
    tool = PresentOrderConfirmationTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog())
    result = await _present(tool, ctx, 48500)
    assert result["queued"] is False
    assert result["error"] == "price_mismatch"
    assert result["expected"] == {"trilogia-del-terror": 49500}
    assert "$49.500" in result["message"]
    assert _intents(path) == []


@pytest.mark.asyncio
async def test_ad_price_is_rejected_with_guidance(ctx, _isolate_vault_dir: Path) -> None:
    """El run real: el LLM pasó 45.000 (anuncio). El envelope le pide aclarar
    el precio vigente al cliente antes de volver a presentar."""
    path = _seed(_isolate_vault_dir)
    tool = PresentOrderConfirmationTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog())
    result = await _present(tool, ctx, 45000)
    assert result["error"] == "price_mismatch"
    assert "45.000" in result["message"] and "49.500" in result["message"]
    assert "aclár" in result["message"].lower() or "aclar" in result["message"].lower()
    assert _intents(path) == []


@pytest.mark.asyncio
async def test_exact_catalog_price_is_accepted(ctx, _isolate_vault_dir: Path) -> None:
    path = _seed(_isolate_vault_dir)
    tool = PresentOrderConfirmationTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog())
    result = await _present(tool, ctx, 49500)
    assert result["queued"] is True
    (intent,) = _intents(path)
    assert intent["params"]["subtotal_cop"] == 49500


@pytest.mark.asyncio
async def test_live_price_from_verification_ledger_is_accepted(ctx, _isolate_vault_dir: Path) -> None:
    """Medusa cambió a 50.000 y el snapshot sigue en 49.500: el precio live que
    dejó `verify_order_for_checkout` en el ledger es válido (el LLM se lo
    informó al cliente); 49.500 (snapshot) también. Cualquier otro, no."""
    path = _seed(_isolate_vault_dir, {
        "checkout_verification": {
            "verified_at_ms": 1,
            "items": {"trilogia-del-terror": {"snapshot_price_cop": 49500, "live_price_cop": 50000,
                                               "unit_price_cop": 50000, "quantity": 1}},
            "quoted_amounts_mismatch": [],
        },
    })
    tool = PresentOrderConfirmationTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog())
    assert (await _present(tool, ctx, 50000))["queued"] is True
    assert len(_intents(path)) == 1
    rejected = await _present(tool, ctx, 47000)
    assert rejected["error"] == "price_mismatch"
    assert len(_intents(path)) == 1


@pytest.mark.asyncio
async def test_summary_reminds_to_clarify_a_quoted_wrong_price(ctx, _isolate_vault_dir: Path) -> None:
    """verify dejó `quoted_amounts_mismatch=[45000]`: aunque el precio pasado
    sea el correcto, el envelope recuerda aclarar el cambio al cliente."""
    _seed(_isolate_vault_dir, {
        "checkout_verification": {
            "verified_at_ms": 1,
            "items": {"trilogia-del-terror": {"snapshot_price_cop": 49500, "live_price_cop": 49500,
                                               "unit_price_cop": 49500, "quantity": 1}},
            "quoted_amounts_mismatch": [45000],
        },
    })
    tool = PresentOrderConfirmationTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog())
    result = await _present(tool, ctx, 49500)
    assert result["queued"] is True
    assert "$45.000" in result["summary"] and "$49.500" in result["summary"]
