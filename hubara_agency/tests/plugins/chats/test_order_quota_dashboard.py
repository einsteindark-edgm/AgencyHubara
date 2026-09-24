""""Crear pedido" del dashboard con cupo por unidad (Fase 6 de CUPONES_PLAN.md).

El formulario del chat intervenido consume el cupo IGUAL que el bot: el
sugerido muestra qué línea lleva descuento (color y aroma de cada ítem), y el
registro reparte con las mismas reglas, bajo el mismo candado por código.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.catalog.dtos import (
    CatalogManifestDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    SearchResult,
)
from src.platform.catalog.errors import ProductNotFoundError
from src.platform.orders.port import DiscountedUnits, OrderRegistrationResult
from src.platform.promotions.port import PromotionDTO
from src.platform.promotions.quota_lock import VaultQuotaLock
from src.platform.promotions.quota_store import FakePromoQuotaStore
from src.platform.promotions.quotas import PromoUnitQuota
from src.plugins.chats.api import order_intake, session_actions
from src.plugins.chats.api.order_intake import OrderIntakeDeps
from src.plugins.chats.api.session_actions import SessionActionsDeps

_S = "wa_1000000001"
_Q = "q_rosado_cafe"
_CUBO = CatalogProductDTO(
    id="prod_cubo", handle="cubo-love", title="Cubo Love", status="published",
    variants=[CatalogVariantDTO(id="v_cubo", title="Unico",
                                prices=[CatalogPriceDTO(amount="21000", currency_code="cop")])],
    tags=["Color: Rosado", "Color: Azul", "Aroma: Café", "Aroma: Lavanda"],
)


class _Catalog:
    _by = {"cubo-love": _CUBO}

    async def get_by_handle(self, handle: str):
        if handle not in self._by:
            raise ProductNotFoundError(handle)
        return self._by[handle]

    async def search(self, q: str = "", *, limit: int = 10, category: str | None = None):
        results = list(self._by.values())[:limit]
        return SearchResult(query=q, count=len(results), truncated=False, stale=False,
                            manifest=CatalogManifestDTO(version="v", fetched_at="t", product_count=len(results)),
                            results=results)


_AMOR26 = PromotionDTO(
    id="promo_amor26", code="AMOR26", discount_type="percentage", value=10, currency_code=None,
    target_type="items", allocation="across", max_quantity=None,
    product_ids=("prod_cubo",), variant_ids=(), collection_ids=(), min_subtotal_cop=None,
    is_automatic=False, status="active", starts_at_ms=None, ends_at_ms=None,
    budget_type=None, budget_limit=None, budget_used=None, description="AMOR Y AMISTAD 2026",
)


def _quotas() -> FakePromoQuotaStore:
    store = FakePromoQuotaStore()
    store.replace("promo_amor26", "AMOR26",
                  [PromoUnitQuota(_Q, "promo_amor26", "AMOR26", "prod_cubo", "cubo-love", "Cubo Love",
                                  "Rosado", "Café", 5, "2026-09-23T17:00:00Z", "ana")],
                  show_units_left=True, actor="ana", now_iso="2026-09-23T17:00:00Z")
    return store


@dataclass
class _Sales:
    sold: dict[str, int] = field(default_factory=dict)

    async def sold_units(self, *, since: datetime) -> dict[str, int]:
        return dict(self.sold)


@dataclass
class _Port:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def register_order(self, **kw: Any) -> OrderRegistrationResult:
        self.calls.append(kw)
        return OrderRegistrationResult(success=True, order_id="order_1", provider="medusa",
                                       raw_payload={"display_id": 50, "items": [{"title": "Cubo Love"}]})


def _episode(**extra: Any) -> dict[str, Any]:
    promo = {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(_AMOR26).items()}
    return {"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None,
            "applied_coupon": {"code": "AMOR26", "applied_at_ms": 1, "promotion": promo}, **extra}


def _write_metadata(vault: Path, metadata: dict[str, Any]) -> None:
    (vault / _S).mkdir(parents=True, exist_ok=True)
    (vault / _S / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")


# --- /order ------------------------------------------------------------------------


def _orders_client(vault: Path, port: _Port, sales: _Sales) -> TestClient:
    async def flush(session_key: str) -> int:
        return 0

    async def notify(*_: Any) -> None:
        return None

    deps = SessionActionsDeps(vault_dir=vault, catalog=_Catalog(), order_port=port, flush=flush,
                              notify_episode_closed=notify, quotas=_quotas(), sales=sales,
                              quota_lock=VaultQuotaLock(vault))
    app = FastAPI()
    app.include_router(session_actions.router, prefix="/api/chats")
    app.dependency_overrides[session_actions.get_session_actions_deps] = lambda: deps
    return TestClient(app)


_SHIP = {"city": "Bogotá", "neighborhood": "Chapinero", "address": "Cl 1 # 2-3", "phone": "3001234567",
         "receiver_name": "Ana Pérez"}


def test_dashboard_create_order_consumes_quota_like_the_bot(tmp_path: Path) -> None:
    _write_metadata(tmp_path, {"episodes": [_episode()]})
    port = _Port()
    client = _orders_client(tmp_path, port, _Sales({_Q: 4}))  # queda 1

    res = client.post(f"/api/chats/session-actions/{_S}/order", json={
        "items": [{"handle": "cubo-love", "quantity": 2, "color": "Rosado", "aroma": "Café"}],
        "shipping": _SHIP, "payment_method": "transfer", "send_payment_instructions": False,
    })

    assert res.status_code == 200, res.text
    assert res.json()["registered"] is True, res.json()
    (call,) = port.calls
    assert call["items"][0].discounted_units == (DiscountedUnits(1, 2100, quota_id=_Q),)
    assert (call["coupon_code"], call["discount_cop"]) == ("AMOR26", 2100)
    assert call["total_cop"] == 42000 + call["shipping_cop"] - 2100


def test_dashboard_create_order_rejects_a_color_the_product_does_not_have(tmp_path: Path) -> None:
    _write_metadata(tmp_path, {"episodes": [_episode()]})
    port = _Port()
    client = _orders_client(tmp_path, port, _Sales())

    res = client.post(f"/api/chats/session-actions/{_S}/order", json={
        "items": [{"handle": "cubo-love", "quantity": 1, "color": "Verde", "aroma": "Café"}],
        "shipping": _SHIP, "payment_method": "transfer",
    })

    body = res.json()
    assert body["registered"] is False
    assert body["error_detail"] == "invalid_variant_attribute"
    assert "Verde" in body["problems"][0]
    assert port.calls == []


# --- /order-intake/suggest -----------------------------------------------------------


@dataclass
class _LLM:
    reply: str
    model: str = "fake"

    async def extract(self, prompt: str) -> str:
        return self.reply


def test_order_intake_prefill_shows_quota_discount_per_line(tmp_path: Path) -> None:
    draft = {"items": [{"producto": "Cubo Love", "color": "Rosado", "aroma": "Café", "cantidad": 2}]}
    _write_metadata(tmp_path, {"episodes": [_episode(order_draft=draft)]})
    (tmp_path / _S / "sessions").mkdir(parents=True, exist_ok=True)
    (tmp_path / _S / "sessions" / f"{_S}.jsonl").write_text(
        json.dumps({"timestamp": "2026-09-23T15:00:00+00:00", "role": "user",
                    "content": "Quiero 2 Cubo Love rosado con aroma a café"}) + "\n",
        encoding="utf-8",
    )
    reply = json.dumps({"items": [{"handle": "cubo-love", "quantity": 2}], "shipping": {}})
    deps = OrderIntakeDeps(vault_dir=tmp_path, catalog=_Catalog(), llm=_LLM(reply),
                           quotas=_quotas(), sales=_Sales({_Q: 4}))
    app = FastAPI()
    app.include_router(order_intake.router, prefix="/api/chats")
    app.dependency_overrides[order_intake.get_order_intake_deps] = lambda: deps

    body = TestClient(app).post(f"/api/chats/order-intake/{_S}/suggest").json()

    [item] = body["items"]
    assert (item["color"], item["aroma"]) == ("Rosado", "Café")
    assert (item["colors"], item["aromas"]) == (["Rosado", "Azul"], ["Café", "Lavanda"])
    assert (item["coupon_units"], item["coupon_discount_cop"]) == (1, 2100)
    assert (body["coupon_code"], body["discount_cop"]) == ("AMOR26", 2100)
