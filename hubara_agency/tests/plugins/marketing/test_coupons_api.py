"""Central de cupones — API de Marketing (Fase 7.1 de CUPONES_PLAN.md).

El dashboard es la central: crea, edita, pausa y borra cupones de porcentaje
en Medusa (por el `PromotionsAdminPort`), guarda el cupo por unidad en el
vault y registra quién hizo cada cambio. Todo con los dobles oficiales del
SDK: nunca toca Medusa real.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import src.plugins.marketing.api.coupons as coupons_api
from src.sdk.catalogkit import CatalogProductDTO
from src.sdk.connectorkit import (
    FakeCouponAuditLog,
    FakePromoQuotaStore,
    FakePromotionsAdmin,
)

_NOW = datetime(2026, 9, 23, 17, 0, tzinfo=timezone.utc)

_CUBO = CatalogProductDTO(
    id="prod_cubo", handle="cubo-love", title="Cubo Love", status="published",
    tags=["Color: Rosado", "Color: Azul", "Aroma: Café", "Aroma: Lavanda"],
)
_VASO = CatalogProductDTO(
    id="prod_vaso", handle="vaso", title="Vaso", status="published", tags=["Color: Blanco"],
)


@dataclass
class _Catalog:
    products: list[CatalogProductDTO]

    async def search(self, q: str, *, limit: int = 10, category: str | None = None) -> Any:
        return type("R", (), {"results": self.products[:limit]})()


@dataclass
class _Sales:
    """Ventas del cupón: pedidos crudos de Medusa (como el lector real)."""

    orders: list[dict[str, Any]] = field(default_factory=list)
    down: bool = False

    async def orders_since(self, since: datetime) -> list[dict[str, Any]]:
        if self.down:
            from src.sdk.connectorkit import PromotionsUnavailableError

            raise PromotionsUnavailableError("down")
        return self.orders

    async def sold_units(self, *, since: datetime) -> dict[str, int]:
        from src.sdk.connectorkit import sold_units_by_quota

        return sold_units_by_quota(await self.orders_since(since))

    async def results(self, code: str, *, since: datetime) -> Any:
        from src.sdk.connectorkit import coupon_results

        return coupon_results(code, await self.orders_since(since))


@dataclass
class World:
    admin: FakePromotionsAdmin
    store: FakePromoQuotaStore
    audit: FakeCouponAuditLog
    sales: _Sales
    client: TestClient


def _world(monkeypatch, *, seed: list[dict[str, Any]] | None = None, app: FastAPI | None = None) -> World:
    admin = FakePromotionsAdmin(seed, clock=lambda: _NOW)
    store = FakePromoQuotaStore()
    audit = FakeCouponAuditLog()
    sales = _Sales()
    monkeypatch.setattr(coupons_api, "promotions_admin", lambda: admin)
    monkeypatch.setattr(coupons_api, "quota_store", lambda: store)
    monkeypatch.setattr(coupons_api, "audit_log", lambda: audit)
    monkeypatch.setattr(coupons_api, "sales_reader", lambda: sales)
    monkeypatch.setattr(coupons_api, "catalog", lambda: _Catalog([_CUBO, _VASO]))
    monkeypatch.setattr(coupons_api, "now", lambda: _NOW)
    if app is None:
        app = FastAPI()
        app.include_router(coupons_api.router, prefix="/api/marketing")
    return World(admin, store, audit, sales, TestClient(app))


_BODY = {
    "code": "amor27",
    "campaign_name": "AMOR Y AMISTAD 2026",
    "percentage": 10,
    "products": ["prod_cubo"],
    "starts_on": "2026-09-22",
    "ends_on": "2026-09-27",
    "status": "active",
}


def _create(w: World, **overrides: Any) -> dict[str, Any]:
    res = w.client.post("/api/marketing/coupons", json={**_BODY, **overrides})
    assert res.status_code == 201, res.text
    return res.json()


# --- Crear ------------------------------------------------------------------


def test_post_coupon_creates_it_in_medusa_and_audits_actor(monkeypatch) -> None:
    from src.platform import auth, config

    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "pool-1")
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", "client-1")
    monkeypatch.setattr(auth, "_verify_token", lambda token: {"username": "ana.perez"})
    app = FastAPI()
    app.include_router(
        coupons_api.router, prefix="/api/marketing", dependencies=[Depends(auth.require_auth)]
    )
    w = _world(monkeypatch, app=app)

    res = w.client.post(
        "/api/marketing/coupons",
        # Un "actor" en el cuerpo NO cuenta: el registro usa la sesión verificada.
        json={**_BODY, "actor": "impostor"},
        headers={"Authorization": "Bearer jwt-de-ana"},
    )

    assert res.status_code == 201, res.text
    coupon = res.json()
    assert coupon["code"] == "AMOR27"
    assert coupon["state"] == "active" and coupon["manageable"] is True
    assert coupon["products"] == ["prod_cubo"]
    assert (coupon["starts_on"], coupon["ends_on"]) == ("2026-09-22", "2026-09-27")
    assert [v.code for v in await_(w.admin.list_coupons())] == ["AMOR27"]
    [entry] = w.audit.entries()
    assert (entry.actor, entry.action, entry.code) == ("ana.perez", "create", "AMOR27")


def await_(coro):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)


def test_post_coupon_invalid_percentage_returns_422_with_reason(monkeypatch) -> None:
    w = _world(monkeypatch)

    res = w.client.post("/api/marketing/coupons", json={**_BODY, "percentage": 0})

    assert res.status_code == 422
    assert res.json()["detail"]["field"] == "percentage"
    assert "entre 1 y 100" in res.json()["detail"]["message"]
    assert w.audit.entries() == []


def test_post_coupon_unknown_product_returns_422(monkeypatch) -> None:
    w = _world(monkeypatch)

    res = w.client.post("/api/marketing/coupons", json={**_BODY, "products": ["prod_nope"]})

    assert res.status_code == 422
    assert res.json()["detail"]["field"] == "products"


def test_post_coupon_taken_code_returns_409(monkeypatch) -> None:
    w = _world(monkeypatch)
    _create(w)

    res = w.client.post("/api/marketing/coupons", json=_BODY)

    assert res.status_code == 409
    assert res.json()["detail"]["message"] == "Ese código ya existe."


def test_medusa_down_returns_503_and_creates_nothing(monkeypatch) -> None:
    from src.sdk.connectorkit import PromotionsUnavailableError

    w = _world(monkeypatch)

    async def down(*a, **k):
        raise PromotionsUnavailableError("timeout")

    monkeypatch.setattr(w.admin, "create_coupon", down)
    monkeypatch.setattr(w.admin, "list_coupons", down)

    assert w.client.post("/api/marketing/coupons", json=_BODY).status_code == 503
    assert w.client.get("/api/marketing/coupons").status_code == 503
    assert w.audit.entries() == []


# --- Listar / detalle -----------------------------------------------------------


def _tagged_amor26() -> dict[str, Any]:
    return {
        "id": "promo_amor26", "code": "AMOR26", "type": "standard", "is_automatic": False,
        "status": "active", "campaign_id": "camp_1",
        "application_method": {
            "id": "am_1", "type": "percentage", "value": 10, "target_type": "items",
            "allocation": "each", "max_quantity": 10,
            "target_rules": [
                {"id": "r1", "attribute": "items.product.id", "operator": "in", "values": [{"value": "prod_cubo"}]},
                {"id": "r2", "attribute": "items.product.tags.id", "operator": "in", "values": [{"value": "ptag_1"}]},
            ],
        },
        "rules": [],
        "campaign": {"id": "camp_1", "name": "AMOR Y AMISTAD 2026", "campaign_identifier": "AMOR26",
                     "starts_at": "2026-09-22T05:00:00.000Z", "ends_at": "2026-09-27T05:00:00.000Z"},
    }


def test_get_coupons_lists_state_manageable_and_units_left(monkeypatch) -> None:
    w = _world(monkeypatch, seed=[_tagged_amor26()])
    w.client.put("/api/marketing/coupons/promo_amor26/units", json={
        "rows": [{"product_id": "prod_cubo", "color": "Rosado", "aroma": "Café", "units": 5}],
        "show_units_left": True,
    })
    quota_id = w.store.get("promo_amor26").quotas[0].id
    w.sales.orders = [_order_with_quota(quota_id, 2)]

    body = w.client.get("/api/marketing/coupons").json()

    [coupon] = body["coupons"]
    assert coupon["code"] == "AMOR26"
    assert coupon["manageable"] is False
    assert "etiquetas" in coupon["unmanageable_reason"]
    assert coupon["units"] == {"total": 5, "left": 3}
    assert body["unavailable"] is False


def test_get_coupon_detail_has_units_and_changes(monkeypatch) -> None:
    w = _world(monkeypatch)
    created = _create(w)

    res = w.client.get(f"/api/marketing/coupons/{created['promotion_id']}")

    assert res.status_code == 200
    body = res.json()
    assert body["coupon"]["code"] == "AMOR27"
    assert body["units"] == {"rows": [], "show_units_left": True, "unavailable": False}
    assert [c["action"] for c in body["changes"]] == ["create"]
    assert w.client.get("/api/marketing/coupons/promo_nope").status_code == 404


# --- Editar -------------------------------------------------------------------


def test_patch_coupon_updates_only_sent_fields_and_audits(monkeypatch) -> None:
    w = _world(monkeypatch)
    created = _create(w)

    res = w.client.patch(
        f"/api/marketing/coupons/{created['promotion_id']}", json={"percentage": 15, "ends_on": "2026-09-30"}
    )

    assert res.status_code == 200, res.text
    assert (res.json()["percentage"], res.json()["ends_on"], res.json()["products"]) == (15, "2026-09-30", ["prod_cubo"])
    change = w.audit.entries()[0]
    assert change.action == "update"
    assert change.detail == {"percentage": [10, 15], "ends_on": ["2026-09-27", "2026-09-30"]}


def test_patch_coupon_code_refused_unless_draft(monkeypatch) -> None:
    w = _world(monkeypatch)
    active = _create(w)
    draft = _create(w, code="BORRADOR1", status="draft")

    refused = w.client.patch(f"/api/marketing/coupons/{active['promotion_id']}", json={"code": "AMOR28"})
    renamed = w.client.patch(f"/api/marketing/coupons/{draft['promotion_id']}", json={"code": "BORRADOR2"})

    assert refused.status_code == 422
    assert refused.json()["detail"]["field"] == "code"
    assert renamed.status_code == 200 and renamed.json()["code"] == "BORRADOR2"


def test_patch_unmanageable_coupon_returns_409_with_reason(monkeypatch) -> None:
    w = _world(monkeypatch, seed=[_tagged_amor26()])

    res = w.client.patch("/api/marketing/coupons/promo_amor26", json={"percentage": 20})
    status = w.client.post("/api/marketing/coupons/promo_amor26/status", json={"status": "inactive"})

    assert res.status_code == 409 and "etiquetas" in res.json()["detail"]["message"]
    assert status.status_code == 409
    assert w.admin.medusa.writes == []


def test_status_pauses_and_resumes(monkeypatch) -> None:
    w = _world(monkeypatch)
    created = _create(w)

    paused = w.client.post(f"/api/marketing/coupons/{created['promotion_id']}/status", json={"status": "inactive"})
    resumed = w.client.post(f"/api/marketing/coupons/{created['promotion_id']}/status", json={"status": "active"})
    bad = w.client.post(f"/api/marketing/coupons/{created['promotion_id']}/status", json={"status": "draft"})

    assert (paused.json()["state"], resumed.json()["state"]) == ("paused", "active")
    assert bad.status_code == 422
    assert [e.action for e in w.audit.entries()][:2] == ["set_status", "set_status"]


# --- Borrar -------------------------------------------------------------------


def _order_with_quota(quota_id: str | None, qty: int, *, code: str = "AMOR26", oid: str = "order_1") -> dict[str, Any]:
    meta: dict[str, Any] = {"coupon_code": code, "list_unit_price_cop": 21000, "discount_unit_cop": 2100}
    if quota_id:
        meta["coupon_quota_id"] = quota_id
    return {
        "id": oid, "display_id": 45, "status": "draft", "created_at": "2026-09-23T18:00:00Z",
        "metadata": {"coupon_code": code, "discount_cop": 2100 * qty},
        "items": [{"quantity": qty, "unit_price": 18900, "metadata": meta}],
    }


def test_delete_coupon_with_sales_returns_409(monkeypatch) -> None:
    w = _world(monkeypatch)
    draft = _create(w, code="BORRADOR1", status="draft")
    w.sales.orders = [_order_with_quota(None, 1, code="BORRADOR1")]

    res = w.client.delete(f"/api/marketing/coupons/{draft['promotion_id']}")

    assert res.status_code == 409
    assert "pausalo" in res.json()["detail"]["message"].lower()


def test_delete_draft_without_sales_removes_coupon_and_its_units(monkeypatch) -> None:
    w = _world(monkeypatch)
    draft = _create(w, code="BORRADOR1", status="draft")
    pid = draft["promotion_id"]
    w.client.put(f"/api/marketing/coupons/{pid}/units", json={
        "rows": [{"product_id": "prod_cubo", "color": "Rosado", "aroma": "Café", "units": 2}],
    })

    res = w.client.delete(f"/api/marketing/coupons/{pid}")

    assert res.status_code == 204
    assert w.client.get(f"/api/marketing/coupons/{pid}").status_code == 404
    assert w.store.get(pid).quotas == ()
    assert w.audit.entries()[0].action == "delete"


def test_delete_active_coupon_returns_409(monkeypatch) -> None:
    w = _world(monkeypatch)
    active = _create(w)

    res = w.client.delete(f"/api/marketing/coupons/{active['promotion_id']}")

    assert res.status_code == 409


# --- Cupo por unidad ------------------------------------------------------------


def test_put_units_saves_rows_with_sold_and_left(monkeypatch) -> None:
    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]

    res = w.client.put(f"/api/marketing/coupons/{pid}/units", json={
        "rows": [{"product_id": "prod_cubo", "color": "rosado", "aroma": "cafe", "units": 5}],
        "show_units_left": False,
    })

    assert res.status_code == 200, res.text
    [row] = res.json()["rows"]
    assert (row["title"], row["color"], row["aroma"], row["units"], row["sold"], row["units_left"]) == (
        "Cubo Love", "Rosado", "Café", 5, 0, 5,
    )
    assert res.json()["show_units_left"] is False
    assert w.audit.entries()[0].action == "units"


def test_put_units_rejects_color_not_in_product_tags(monkeypatch) -> None:
    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]

    res = w.client.put(f"/api/marketing/coupons/{pid}/units", json={
        "rows": [
            {"product_id": "prod_cubo", "color": "Rosado", "aroma": "Café", "units": 5},
            {"product_id": "prod_cubo", "color": "Verde", "aroma": "Café", "units": 1},
        ],
    })

    assert res.status_code == 422
    assert [(e["row"], e["field"]) for e in res.json()["detail"]["rows"]] == [(1, "color")]
    assert w.store.get(pid).quotas == ()  # nada a medias


def test_put_units_rejects_product_outside_coupon(monkeypatch) -> None:
    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]  # solo prod_cubo

    res = w.client.put(f"/api/marketing/coupons/{pid}/units", json={
        "rows": [{"product_id": "prod_vaso", "color": "Blanco", "units": 1}],
    })

    assert res.status_code == 422
    assert res.json()["detail"]["rows"][0]["field"] == "product_id"


def test_units_on_a_read_only_coupon_are_allowed(monkeypatch) -> None:
    # AMOR26 (regla por etiquetas) no se edita en Medusa desde la central,
    # pero su cupo vive en Hubara: se puede poner igual.
    w = _world(monkeypatch, seed=[_tagged_amor26()])

    res = w.client.put("/api/marketing/coupons/promo_amor26/units", json={
        "rows": [{"product_id": "prod_cubo", "color": "Rosado", "aroma": "Café", "units": 5}],
    })

    assert res.status_code == 200
    assert w.admin.medusa.writes == []


def test_units_show_oversold_warning_when_units_below_sold(monkeypatch) -> None:
    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]
    w.client.put(f"/api/marketing/coupons/{pid}/units", json={
        "rows": [{"product_id": "prod_cubo", "color": "Rosado", "aroma": "Café", "units": 1}],
    })
    quota_id = w.store.get(pid).quotas[0].id
    w.sales.orders = [_order_with_quota(quota_id, 3, code="AMOR27")]

    [row] = w.client.get(f"/api/marketing/coupons/{pid}/units").json()["rows"]

    assert (row["sold"], row["units_left"], row["oversold"]) == (3, 0, True)


# --- Resultados -------------------------------------------------------------------


def test_get_coupon_sales_returns_orders_discount_and_units(monkeypatch) -> None:
    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]
    w.sales.orders = [
        _order_with_quota("q_x", 2, code="AMOR27", oid="order_1"),
        _order_with_quota(None, 1, code="AMOR27", oid="order_2"),
    ]

    body = w.client.get(f"/api/marketing/coupons/{pid}/sales").json()

    assert (body["orders"], body["discount_cop"], body["quota_units"]) == (2, 3 * 2100, 2)
    assert [s["order_id"] for s in body["sales"]] == ["order_1", "order_2"]


def test_coupon_products_lists_colors_and_aromas(monkeypatch) -> None:
    w = _world(monkeypatch)

    body = w.client.get("/api/marketing/coupon-products").json()

    assert body["products"][0] == {
        "id": "prod_cubo", "handle": "cubo-love", "title": "Cubo Love",
        "colors": ["Rosado", "Azul"], "aromas": ["Café", "Lavanda"],
    }


def test_marketing_router_mounts_the_coupon_central(monkeypatch) -> None:
    import src.plugins.marketing.api as marketing_api

    _world(monkeypatch)
    app = FastAPI()
    app.include_router(marketing_api.router, prefix="/api/marketing")

    assert TestClient(app).get("/api/marketing/coupons").status_code == 200
