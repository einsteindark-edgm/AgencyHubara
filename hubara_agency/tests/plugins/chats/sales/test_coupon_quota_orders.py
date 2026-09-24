"""Cupo por unidad en la confirmación y el registro del pedido (Fase 5,
parte 2 de CUPONES_PLAN.md).

Con cupo, el descuento va SOLO a las unidades de la combinación producto +
color + aroma que quedan; el resto a precio normal (D2 parcial). El reparto
confirmado se guarda y `register_order` lo relee BAJO EL CANDADO del código:
si otro cliente se llevó la última unidad, no crea el draft y devuelve
`quota_changed` con el total nuevo.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO, ProductNotFoundError
from src.platform.catalog.dtos import CatalogManifestDTO, SearchResult
from src.platform.orders.port import DiscountedUnits, OrderRegistrationResult
from src.platform.promotions.port import PromotionDTO, PromotionsUnavailableError
from src.platform.promotions.quota_lock import VaultQuotaLock
from src.platform.promotions.quota_store import FakePromoQuotaStore
from src.platform.promotions.quotas import PromoUnitQuota
from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool
from src.plugins.chats.agent.sales.tools.ui_intents import PresentOrderConfirmationTool

KEY = "wa_test_quota_orders"
_TAGS = ["Color: Rosado", "Color: Azul", "Aroma: Café", "Aroma: Lavanda"]


def _product(handle: str, title: str, price: str, pid: str, tags: list[str]) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=pid, handle=handle, title=title, status="published", tags=tags,
        variants=[CatalogVariantDTO(id=f"variant_{handle}", title="Unico", sku=handle.upper(),
                                    prices=[CatalogPriceDTO(amount=price, currency_code="cop")])],
    )


class _Catalog:
    products = {
        "cubo-love": _product("cubo-love", "Cubo Love", "21000", "prod_cubo", _TAGS),
        "vela-buda": _product("vela-buda", "Vela Buda", "40000", "prod_buda", []),
    }

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        try:
            return self.products[handle]
        except KeyError:
            raise ProductNotFoundError(handle) from None

    async def search(self, q: str, *, limit: int = 10, category: str | None = None) -> SearchResult:
        products = list(self.products.values())
        return SearchResult(
            query=q, count=len(products), truncated=False, stale=False,
            manifest=CatalogManifestDTO(version="t", fetched_at="2026-01-01T00:00:00Z", product_count=len(products)),
            results=products,
        )


_AMOR26 = PromotionDTO(
    id="promo_amor26", code="AMOR26", discount_type="percentage", value=10, currency_code=None,
    target_type="items", allocation="across", max_quantity=None,
    product_ids=("prod_cubo",), variant_ids=(), collection_ids=(), min_subtotal_cop=None,
    is_automatic=False, status="active", starts_at_ms=None, ends_at_ms=None,
    budget_type=None, budget_limit=None, budget_used=None, description="AMOR Y AMISTAD 2026",
)


def _snapshot(promo: PromotionDTO) -> dict[str, Any]:
    return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(promo).items()}


def _seed(vault: Path, key: str = KEY, *, draft_items: list[dict[str, Any]] | None = None) -> Path:
    episode: dict[str, Any] = {
        "episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None,
        "applied_coupon": {"code": "AMOR26", "promotion": _snapshot(_AMOR26), "applied_at_ms": 1},
    }
    if draft_items is not None:
        episode["order_draft"] = {"items": draft_items}
    path = vault / key / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"episodes": [episode]}, ensure_ascii=False), encoding="utf-8")
    return path


_Q = "q_rosado_cafe"


def _quotas(units: int = 5) -> FakePromoQuotaStore:
    store = FakePromoQuotaStore()
    store.replace(
        "promo_amor26", "AMOR26",
        [PromoUnitQuota(_Q, "promo_amor26", "AMOR26", "prod_cubo", "cubo-love", "Cubo Love",
                        "Rosado", "Café", units, "2026-09-23T17:00:00Z", "ana")],
        show_units_left=True, actor="ana", now_iso="2026-09-23T17:00:00Z",
    )
    return store


@dataclass
class _Sales:
    sold: dict[str, int] = field(default_factory=dict)
    down: bool = False

    async def sold_units(self, *, since: datetime) -> dict[str, int]:
        if self.down:
            raise PromotionsUnavailableError("timeout")
        await asyncio.sleep(0)
        return dict(self.sold)


def _ctx(key: str = KEY) -> ToolContext:
    return ToolContext(session_key=key, channel="whatsapp", chat_id=key)


def _confirm_tool(vault: Path, sales: _Sales) -> PresentOrderConfirmationTool:
    return PresentOrderConfirmationTool(workspace=str(vault), catalog=_Catalog(), quotas=_quotas(), sales=sales)


async def _confirm(tool: PresentOrderConfirmationTool, ctx: ToolContext, items: list[dict[str, Any]]) -> dict[str, Any]:
    return json.loads(
        await tool.execute_with_context(
            ctx, items=items, shipping_cop=7900,
            shipping_address_summary="Calle 1, Bogotá", payment_method="transfer",
        )
    )


def _cubo(qty: int = 1, **attrs: Any) -> dict[str, Any]:
    return {"handle": "cubo-love", "quantity": qty, "unit_price_cop": 21000, **attrs}


# --- Confirmación ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_present_order_confirmation_rejects_aroma_not_in_product(_isolate_vault_dir) -> None:
    path = _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales()), _ctx(),
                         [_cubo(color="Rosado", aroma="Vainilla")])

    assert env["queued"] is False
    assert env["error"] == "invalid_variant_attribute"
    assert "Vainilla" in env["message"] and "Café" in env["message"]
    assert "total_cop" not in env
    assert "pending_ui_intents" not in json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_confirmation_discounts_only_quota_units_in_mixed_order(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales({_Q: 2})), _ctx(), [
        _cubo(color="rosado", aroma="cafe"),
        _cubo(color="Azul", aroma="Lavanda"),
    ])

    assert env["queued"] is True, env
    assert env["discount_cop"] == 2100
    assert env["total_cop"] == 42000 + 7900 - 2100
    assert "1 × Cubo Love Rosado · Café con AMOR26 (−$2.100)" in env["summary"]


@pytest.mark.asyncio
async def test_confirmation_is_partial_when_asking_more_than_left(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales({_Q: 4})), _ctx(),
                         [_cubo(2, color="Rosado", aroma="Café")])

    assert env["discount_cop"] == 2100
    assert "1 a precio normal" in env["summary"]


@pytest.mark.asyncio
async def test_confirmation_takes_color_and_aroma_from_the_order_draft(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir,
          draft_items=[{"producto": "cubo love", "color": "Rosado", "aroma": "Café", "cantidad": 1}])

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales()), _ctx(), [_cubo()])

    assert env["discount_cop"] == 2100


@pytest.mark.asyncio
async def test_confirmation_without_attributes_asks_for_them_and_discounts_nothing(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales()), _ctx(), [_cubo()])

    assert env["queued"] is True
    assert "discount_cop" not in env
    assert "color y el aroma" in env["summary"]


@pytest.mark.asyncio
async def test_confirmation_with_sales_unreadable_does_not_discount(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)

    env = await _confirm(_confirm_tool(_isolate_vault_dir, _Sales(down=True)), _ctx(),
                         [_cubo(color="Rosado", aroma="Café")])

    assert "discount_cop" not in env
    assert "no pude confirmar" in env["summary"].lower()


# --- Registro ---------------------------------------------------------------------

_SHIPPING = {"city": "Bogotá", "neighborhood": "Chapinero", "address": "Calle 1 #2-3",
             "phone": "3001234567", "receiver_name": "Ana Pérez"}


@dataclass
class _Port:
    """Port que 'vende' las unidades con cupo que registra (como Medusa)."""

    sales: _Sales
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def register_order(self, **kwargs: Any) -> OrderRegistrationResult:
        await asyncio.sleep(0.02)  # Medusa tarda: sin candado la carrera queda abierta
        self.calls.append(kwargs)
        for item in kwargs["items"]:
            for group in item.discounted_units:
                if group.quota_id:
                    self.sales.sold[group.quota_id] = self.sales.sold.get(group.quota_id, 0) + group.units
        n = len(self.calls)
        return OrderRegistrationResult(success=True, order_id=f"draft_{n}", provider="medusa",
                                       raw_payload={"id": f"draft_{n}", "display_id": 40 + n})


def _register_tool(vault: Path, port: _Port) -> RegisterOrderTool:
    return RegisterOrderTool(
        workspace=str(vault), vault_dir=vault, port=port, catalog=_Catalog(),
        quotas=_quotas(), sales=port.sales, quota_lock=VaultQuotaLock(vault),
    )


async def _register(tool: RegisterOrderTool, ctx: ToolContext, *, total: int) -> dict[str, Any]:
    return json.loads(
        await tool.execute_with_context(
            ctx, items=[_cubo(color="Rosado", aroma="Café")], shipping=_SHIPPING,
            payment_method="transfer", subtotal_cop=21000, shipping_cop=7900, total_cop=total,
        )
    )


_WITH_DISCOUNT = 21000 + 7900 - 2100


@pytest.mark.asyncio
async def test_register_order_sends_the_quota_of_each_discounted_unit(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)
    sales = _Sales()
    await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(), [_cubo(color="Rosado", aroma="Café")])
    port = _Port(sales)

    env = await _register(_register_tool(_isolate_vault_dir, port), _ctx(), total=_WITH_DISCOUNT)

    assert env["registered"] is True, env
    (call,) = port.calls
    assert call["items"][0].discounted_units == (DiscountedUnits(1, 2100, quota_id=_Q),)


@pytest.mark.asyncio
async def test_register_order_quota_changed_returns_new_total_without_draft(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)
    sales = _Sales({_Q: 4})  # queda 1 al confirmar
    await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(), [_cubo(color="Rosado", aroma="Café")])
    sales.sold[_Q] = 5  # otro cliente se llevó la última
    port = _Port(sales)

    env = await _register(_register_tool(_isolate_vault_dir, port), _ctx(), total=_WITH_DISCOUNT)

    assert env["registered"] is False
    assert env["error_detail"] == "quota_changed"
    assert env["new_total_cop"] == 21000 + 7900
    assert "present_order_confirmation" in env["summary"]
    assert port.calls == []


@pytest.mark.asyncio
async def test_register_with_quota_never_sells_the_last_unit_twice(_isolate_vault_dir) -> None:
    """Dos clientes confirmaron la última unidad: uno registra, el otro recibe
    `quota_changed` (sin draft)."""
    sales = _Sales({_Q: 4})
    port = _Port(sales)
    keys = ["wa_test_quota_a", "wa_test_quota_b"]
    for key in keys:
        _seed(_isolate_vault_dir, key)
        await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(key), [_cubo(color="Rosado", aroma="Café")])

    results = await asyncio.gather(*[
        _register(_register_tool(_isolate_vault_dir, port), _ctx(key), total=_WITH_DISCOUNT) for key in keys
    ])

    assert sorted(r["registered"] for r in results) == [False, True]
    assert [r.get("error_detail") for r in results if not r["registered"]] == ["quota_changed"]
    assert len(port.calls) == 1 and sales.sold[_Q] == 5


# --- Segunda revisión: idempotencia en el borde del cupo y fallas cerradas ------------


def _fingerprint(call: dict[str, Any]) -> str:
    from src.platform.orders.medusa_order import _compute_order_fingerprint

    return _compute_order_fingerprint(call["items"], call["total_cop"], call["payment_method"])


@pytest.mark.asyncio
async def test_register_retry_of_the_same_order_at_the_last_unit_is_idempotent(_isolate_vault_dir) -> None:
    """El reintento del MISMO pedido no cuenta su propio draft como vendido:
    pide el mismo pedido (mismo fingerprint → el adapter reusa el draft) en
    vez de responder `quota_changed` y terminar duplicándolo a precio lleno."""
    _seed(_isolate_vault_dir)
    sales = _Sales({_Q: 4})  # queda 1
    await _confirm(_confirm_tool(_isolate_vault_dir, sales), _ctx(), [_cubo(color="Rosado", aroma="Café")])
    port = _Port(sales)
    tool = _register_tool(_isolate_vault_dir, port)

    first = await _register(tool, _ctx(), total=_WITH_DISCOUNT)
    again = await _register(tool, _ctx(), total=_WITH_DISCOUNT)

    assert first["registered"] is True and again["registered"] is True, again
    assert _fingerprint(port.calls[0]) == _fingerprint(port.calls[1])


@pytest.mark.asyncio
async def test_register_without_a_confirmed_split_asks_to_confirm_it(_isolate_vault_dir) -> None:
    """Con cupo, el cliente tiene que haber VISTO el reparto (confirmación)."""
    _seed(_isolate_vault_dir)
    port = _Port(_Sales())

    env = await _register(_register_tool(_isolate_vault_dir, port), _ctx(), total=_WITH_DISCOUNT)

    assert (env["registered"], env["error_detail"]) == (False, "quota_changed")
    assert port.calls == []


@pytest.mark.asyncio
async def test_register_rejects_a_color_the_product_does_not_have(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)
    port = _Port(_Sales())
    tool = _register_tool(_isolate_vault_dir, port)

    env = json.loads(await tool.execute_with_context(
        _ctx(), items=[_cubo(color="Verde", aroma="Café")], shipping=_SHIPPING,
        payment_method="transfer", subtotal_cop=21000, shipping_cop=7900, total_cop=28900,
    ))

    assert (env["registered"], env["error_detail"]) == (False, "invalid_variant_attribute")
    assert "Verde" in env["summary"]
    assert port.calls == []


@pytest.mark.asyncio
async def test_register_with_quota_but_no_lock_fails_closed(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)
    port = _Port(_Sales())
    tool = RegisterOrderTool(workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=port,
                             catalog=_Catalog(), quotas=_quotas(), sales=port.sales, quota_lock=None)

    env = await _register(tool, _ctx(), total=_WITH_DISCOUNT)

    assert (env["registered"], env["error_detail"]) == (False, "quota_unavailable")
    assert port.calls == []


@pytest.mark.asyncio
async def test_register_order_returns_quota_busy_when_the_lock_times_out(_isolate_vault_dir, monkeypatch) -> None:
    import src.plugins.chats.agent.sales.tools.order_registration as reg

    _seed(_isolate_vault_dir)
    monkeypatch.setattr(reg, "_QUOTA_LOCK_TIMEOUT_S", 0.1)
    port = _Port(_Sales())
    lock = VaultQuotaLock(_isolate_vault_dir)

    async with lock.hold("AMOR26", timeout_s=1):
        env = await _register(_register_tool(_isolate_vault_dir, port), _ctx(), total=_WITH_DISCOUNT)

    assert (env["registered"], env["error_detail"]) == (False, "quota_busy")
    assert port.calls == []


@pytest.mark.asyncio
async def test_confirmation_with_quota_but_no_sales_reader_does_not_discount(_isolate_vault_dir) -> None:
    _seed(_isolate_vault_dir)
    tool = PresentOrderConfirmationTool(workspace=str(_isolate_vault_dir), catalog=_Catalog(),
                                        quotas=_quotas(), sales=None)

    env = await _confirm(tool, _ctx(), [_cubo(color="Rosado", aroma="Café")])

    assert env["queued"] is True and "discount_cop" not in env
    assert "no pude confirmar" in env["summary"].lower()
