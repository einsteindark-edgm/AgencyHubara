"""Cupo por unidad en el bot de ventas (Fase 5 de CUPONES_PLAN.md).

Un cupón con filas de cupo aplica SOLO a esas combinaciones (producto +
color + aroma) mientras queden unidades. `apply_coupon` y `list_promotions`
lo dicen con honestidad: qué unidades, cuántas quedan (si el cupón lo
permite, D3) y "agotado" cuando no queda ninguna. Sin poder leer lo vendido,
el cupón con cupo NO se aplica (falla cerrada).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO
from src.platform.catalog.dtos import CatalogManifestDTO, SearchResult
from src.platform.promotions.port import FakePromotionsPort, PromotionDTO
from src.platform.promotions.quota_store import FakePromoQuotaStore
from src.platform.promotions.quotas import PromoUnitQuota
from src.platform.promotions.port import PromotionsUnavailableError
from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools.coupons import ApplyCouponTool, ListPromotionsTool
from src.plugins.chats.agent.sales.use_cases.coupons import applied_coupon

KEY = "wa_test_coupon_quota"
_NOW = 1_790_000_000_000


def _product(handle: str, title: str, price: str, pid: str) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=pid, handle=handle, title=title, status="published",
        variants=[CatalogVariantDTO(id=f"variant_{handle}", title="Unico", sku=handle.upper(),
                                    prices=[CatalogPriceDTO(amount=price, currency_code="cop")])],
        tags=["Color: Rosado", "Color: Azul", "Aroma: Café", "Aroma: Lavanda"],
    )


class _Catalog:
    products = [_product("cubo-love", "Cubo Love", "21000", "prod_cubo"),
                _product("vela-buda", "Vela Buda", "40000", "prod_buda")]

    async def search(self, q: str, *, limit: int = 10, category: str | None = None) -> SearchResult:
        return SearchResult(
            query=q, count=len(self.products), truncated=False, stale=False,
            manifest=CatalogManifestDTO(version="t", fetched_at="2026-01-01T00:00:00Z",
                                        product_count=len(self.products)),
            results=list(self.products),
        )


def _promo(code: str = "AMOR26") -> PromotionDTO:
    return PromotionDTO(
        id="promo_amor26", code=code, discount_type="percentage", value=10, currency_code=None,
        target_type="items", allocation="across", max_quantity=None,
        product_ids=("prod_cubo",), variant_ids=(), collection_ids=(), min_subtotal_cop=None,
        is_automatic=False, status="active", starts_at_ms=None, ends_at_ms=None,
        budget_type=None, budget_limit=None, budget_used=None, description="AMOR Y AMISTAD 2026",
    )


def _row(qid: str, color: str, aroma: str, units: int) -> PromoUnitQuota:
    return PromoUnitQuota(qid, "promo_amor26", "AMOR26", "prod_cubo", "cubo-love", "Cubo Love",
                          color, aroma, units, "2026-09-23T17:00:00Z", "ana")


class _Sales:
    def __init__(self, sold: dict[str, int] | None = None, *, down: bool = False) -> None:
        self.sold = sold or {}
        self.down = down

    async def sold_units(self, *, since: datetime) -> dict[str, int]:
        if self.down:
            raise PromotionsUnavailableError("timeout")
        return self.sold


def _store_with(rows: list[PromoUnitQuota], *, show: bool = True) -> FakePromoQuotaStore:
    store = FakePromoQuotaStore()
    store.replace("promo_amor26", "AMOR26", rows, show_units_left=show, actor="ana", now_iso="2026-09-23T17:00:00Z")
    return store


def _apply_tool(tmp_path: Path, *, quotas: FakePromoQuotaStore, sales: _Sales) -> tuple[ApplyCouponTool, FilesystemMetadataStore]:
    md = FilesystemMetadataStore(tmp_path)
    md.write(KEY, {"episodes": [{"id": "ep_1", "status": "active"}]})
    tool = ApplyCouponTool(
        tmp_path, promotions=FakePromotionsPort([_promo()]), metadata_store=md,
        catalog=_Catalog(), now_ms=lambda: _NOW, quotas=quotas, sales=sales,
    )
    return tool, md


def _ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


@pytest.mark.asyncio
async def test_apply_coupon_lists_eligible_units_with_units_left(tmp_path: Path) -> None:
    store = _store_with([_row("q1", "Rosado", "Café", 5), _row("q2", "Azul", "Lavanda", 2)])
    tool, md = _apply_tool(tmp_path, quotas=store, sales=_Sales({"q1": 2, "q2": 2}))

    out = json.loads(await tool.execute_with_context(_ctx(), code="amor26"))

    assert out["applied"] is True
    # Solo las combinaciones con unidades (Azul · Lavanda ya se vendió toda).
    assert out["units"] == [
        {"title": "Cubo Love", "color": "Rosado", "aroma": "Café", "units_left": 3,
         "price_cop": 21000, "discounted_price_cop": 18900},
    ]
    assert "Cubo Love Rosado · Café" in out["summary"] and "quedan 3" in out["summary"]
    assert "SOLO" in out["summary"]
    snapshot = applied_coupon(md.read(KEY))
    assert snapshot is not None and snapshot["code"] == "AMOR26"


@pytest.mark.asyncio
async def test_apply_coupon_hides_units_left_when_preference_off(tmp_path: Path) -> None:
    store = _store_with([_row("q1", "Rosado", "Café", 5)], show=False)
    tool, _ = _apply_tool(tmp_path, quotas=store, sales=_Sales({"q1": 2}))

    out = json.loads(await tool.execute_with_context(_ctx(), code="AMOR26"))

    assert "units_left" not in out["units"][0]
    assert not re.search(r"quedan \d", out["summary"])
    assert "no le digas cuántas quedan" in out["summary"]


@pytest.mark.asyncio
async def test_apply_coupon_quota_exhausted_is_honest_and_does_not_cut_turn(tmp_path: Path) -> None:
    store = _store_with([_row("q1", "Rosado", "Café", 2)])
    tool, md = _apply_tool(tmp_path, quotas=store, sales=_Sales({"q1": 2}))

    out = json.loads(await tool.execute_with_context(_ctx(), code="AMOR26"))

    assert out["applied"] is False
    assert out["reason"] == "quota_exhausted"
    assert "ya se agotaron" in out["summary"]
    assert "precio normal" in out["summary"]
    # Un rechazo NO corta el turno (el bot tiene que contestarle al cliente).
    assert "tag_closure" not in out and "ends_turn" not in out
    assert applied_coupon(md.read(KEY)) is None


@pytest.mark.asyncio
async def test_apply_coupon_quota_unavailable_fails_closed(tmp_path: Path) -> None:
    store = _store_with([_row("q1", "Rosado", "Café", 5)])
    tool, md = _apply_tool(tmp_path, quotas=store, sales=_Sales(down=True))

    out = json.loads(await tool.execute_with_context(_ctx(), code="AMOR26"))

    assert (out["applied"], out["reason"]) == (False, "quota_unavailable")
    assert applied_coupon(md.read(KEY)) is None


@pytest.mark.asyncio
async def test_apply_coupon_without_quota_rows_behaves_as_before(tmp_path: Path) -> None:
    tool, _ = _apply_tool(tmp_path, quotas=FakePromoQuotaStore(), sales=_Sales(down=True))

    out = json.loads(await tool.execute_with_context(_ctx(), code="AMOR26"))

    assert out["applied"] is True
    assert "units" not in out
    assert out["eligible_products"][0]["title"] == "Cubo Love"


@pytest.mark.asyncio
async def test_list_promotions_shows_units_left_and_marks_exhausted(tmp_path: Path) -> None:
    store = _store_with([_row("q1", "Rosado", "Café", 5)])
    live = ListPromotionsTool(tmp_path, promotions=FakePromotionsPort([_promo()]), catalog=_Catalog(),
                              quotas=store, sales=_Sales({"q1": 1}))
    gone = ListPromotionsTool(tmp_path, promotions=FakePromotionsPort([_promo()]), catalog=_Catalog(),
                              quotas=store, sales=_Sales({"q1": 5}))

    live_out = json.loads(await live.execute_with_context(_ctx()))
    gone_out = json.loads(await gone.execute_with_context(_ctx()))

    [promo] = live_out["promotions"]
    assert promo["units"] == [{"title": "Cubo Love", "color": "Rosado", "aroma": "Café", "units_left": 4,
                               "price_cop": 21000, "discounted_price_cop": 18900}]
    assert promo.get("exhausted") is not True
    [spent] = gone_out["promotions"]
    assert spent["exhausted"] is True and spent["units"] == []
    assert "AMOR26 (agotado" in gone_out["summary"]


def test_sales_worker_gives_coupon_tools_the_quota_dependencies(tmp_path: Path, monkeypatch) -> None:
    """Sin el almacén del cupo y el lector de vendidas, el bot aplicaría un
    cupón con cupo a TODAS las unidades (la central diría otra cosa)."""
    monkeypatch.setenv("MEDUSA_BASE_URL", "http://medusa.test")
    monkeypatch.setenv("MEDUSA_ADMIN_TOKEN", "dummy")
    import src.plugins.chats.workers.sales  # noqa: F401  (registra las tools)
    from src.platform.tool_extensions import _EXTENSIONS  # type: ignore

    for name in ("sales.apply_coupon", "sales.list_promotions"):
        tool = dict(_EXTENSIONS)[name](tmp_path)  # ejecuta el lambda (caza NameError)
        assert tool._quotas is not None and tool._sales is not None, name
