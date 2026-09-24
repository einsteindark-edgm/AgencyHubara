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

import pytest
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
    """Ventas del cupón: pedidos crudos de Medusa (como el lector real: solo
    los creados desde `since`). `scans` registra cada lectura a Medusa."""

    orders: list[dict[str, Any]] = field(default_factory=list)
    down: bool = False
    scans: list[datetime] = field(default_factory=list)

    async def orders_since(self, since: datetime) -> list[dict[str, Any]]:
        if self.down:
            from src.sdk.connectorkit import PromotionsUnavailableError

            raise PromotionsUnavailableError("down")
        self.scans.append(since)
        return [
            o for o in self.orders
            if datetime.fromisoformat(o["created_at"].replace("Z", "+00:00")) >= since
        ]

    async def sold_units(self, *, since: datetime) -> dict[str, int]:
        from src.sdk.connectorkit import sold_units_by_quota

        return sold_units_by_quota(await self.orders_since(since))

    async def results(self, code: str, *, since: datetime) -> Any:
        from src.sdk.connectorkit import coupon_results

        return coupon_results(code, await self.orders_since(since))


@dataclass
class _Reader:
    """`PromotionsPort` sobre la MISMA Medusa en memoria de la central (con
    la traducción de etiquetas que hace el adapter real)."""

    admin: FakePromotionsAdmin
    tags: dict[str, str] = field(default_factory=lambda: {"ptag_1": "Color: Rosado"})

    async def get_by_code(self, code: str):
        from src.platform.promotions.medusa import promotion_from_medusa

        for raw in await self.admin.medusa.list_promotions():
            promo = promotion_from_medusa(raw, tag_values=self.tags)
            if promo is not None and promo.code == code.upper():
                return promo
        return None


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
    monkeypatch.setattr(coupons_api, "promotions_reader", lambda: _Reader(admin))
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
    assert body["units"] == {"rows": [], "show_units_left": True, "unavailable": False, "updated_at": None}
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
    assert "páusalo" in res.json()["detail"]["message"].lower()


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


# --- Revisión de gates: bordes ------------------------------------------------------


def test_units_respect_the_real_scope_of_a_tag_rule_coupon(monkeypatch) -> None:
    """AMOR26 (productos + etiquetas): una fila de un producto que la regla
    por etiquetas deja afuera se rechaza — el bot no puede prometer ese
    descuento."""
    from src.sdk.connectorkit import FakePromotionsPort, PromotionDTO

    promo = PromotionDTO(
        id="promo_amor26", code="AMOR26", discount_type="percentage", value=10, currency_code=None,
        target_type="items", allocation="each", max_quantity=10,
        product_ids=("prod_cubo", "prod_vaso"), variant_ids=(), collection_ids=(), min_subtotal_cop=None,
        is_automatic=False, status="active", starts_at_ms=None, ends_at_ms=None, budget_type=None,
        budget_limit=None, budget_used=None, description=None, tag_values=("Color: Rosado",),
    )
    seed = _tagged_amor26()
    seed["application_method"]["target_rules"][0]["values"].append({"value": "prod_vaso"})
    w = _world(monkeypatch, seed=[seed])
    monkeypatch.setattr(coupons_api, "promotions_reader", lambda: FakePromotionsPort([promo]))

    res = w.client.put("/api/marketing/coupons/promo_amor26/units", json={
        "rows": [
            {"product_id": "prod_cubo", "color": "Rosado", "aroma": "Café", "units": 2},
            # El Vaso no tiene la etiqueta "Color: Rosado": fuera del cupón.
            {"product_id": "prod_vaso", "color": "Blanco", "units": 1},
        ],
    })

    assert res.status_code == 422
    assert [(e["row"], e["field"]) for e in res.json()["detail"]["rows"]] == [(1, "product_id")]


@pytest.mark.parametrize("bad", ["..", "promo%2F1", "a" * 80])
def test_unsafe_promotion_id_is_404(monkeypatch, bad) -> None:
    w = _world(monkeypatch)

    assert w.client.get(f"/api/marketing/coupons/{bad}/units").status_code == 404


def test_catalog_down_is_503_not_500(monkeypatch) -> None:
    w = _world(monkeypatch)

    class Down:
        async def search(self, *a, **k):
            raise RuntimeError("snapshot roto")

    monkeypatch.setattr(coupons_api, "catalog", lambda: Down())

    assert w.client.post("/api/marketing/coupons", json=_BODY).status_code == 503
    assert w.client.get("/api/marketing/coupon-products").status_code == 503


def test_patch_of_a_coupon_deleted_meanwhile_is_404(monkeypatch) -> None:
    from src.sdk.connectorkit import CouponNotFoundError

    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]

    async def gone(*a, **k):
        raise CouponNotFoundError("borrado")

    monkeypatch.setattr(w.admin, "update_coupon", gone)

    assert w.client.patch(f"/api/marketing/coupons/{pid}", json={"percentage": 20}).status_code == 404


def test_unreadable_quota_file_is_503_not_an_empty_quota(monkeypatch) -> None:
    from src.sdk.connectorkit import QuotaStoreError

    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]

    def broken(promotion_id):
        raise QuotaStoreError("roto")

    monkeypatch.setattr(w.store, "get", broken)

    assert w.client.get(f"/api/marketing/coupons/{pid}/units").status_code == 503
    assert w.client.get(f"/api/marketing/coupons/{pid}").status_code == 503


# --- Premortem de la central ---------------------------------------------------------

_ROW = {"product_id": "prod_cubo", "color": "Rosado", "aroma": "Café"}


def _at(monkeypatch, iso: str) -> None:
    """El reloj de la central a esa hora (UTC)."""
    monkeypatch.setattr(coupons_api, "now", lambda: datetime.fromisoformat(iso.replace("Z", "+00:00")))


def _units_url(pid: str) -> str:
    return f"/api/marketing/coupons/{pid}/units"


def _manageable_seed(pid: str, code: str, *, products: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": pid, "code": code, "type": "standard", "is_automatic": False, "status": "active",
        "campaign_id": f"camp_{pid}",
        "application_method": {
            "id": f"am_{pid}", "type": "percentage", "value": 10, "target_type": "items",
            "allocation": "across", "max_quantity": None,
            "target_rules": [
                {"id": f"r_{pid}", "attribute": "items.product.id", "operator": "in",
                 "values": [{"value": p} for p in (products or ["prod_cubo"])]},
            ],
        },
        "rules": [],
        "campaign": {"id": f"camp_{pid}", "name": code, "campaign_identifier": code,
                     "starts_at": "2026-09-22T05:00:00.000Z", "ends_at": "2026-09-28T05:00:00.000Z"},
    }


# A5 / C-5: concurrencia optimista del cupo.


def test_units_carry_updated_at_and_a_stale_editor_gets_409_units_changed(monkeypatch) -> None:
    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]
    assert w.client.get(_units_url(pid)).json()["updated_at"] is None  # nunca se guardó

    _at(monkeypatch, "2026-09-23T17:00:00Z")
    ana = w.client.put(_units_url(pid), json={"rows": [{**_ROW, "units": 5}], "expected_updated_at": None})
    _at(monkeypatch, "2026-09-23T17:05:00Z")
    luis = w.client.put(
        _units_url(pid), json={"rows": [{**_ROW, "units": 3}], "expected_updated_at": ana.json()["updated_at"]}
    )
    _at(monkeypatch, "2026-09-23T17:06:00Z")
    stale = w.client.put(  # Ana seguía viendo la versión de antes de guardar
        _units_url(pid), json={"rows": [{**_ROW, "units": 8}], "expected_updated_at": ana.json()["updated_at"]}
    )

    assert ana.status_code == 200 and ana.json()["updated_at"]
    assert luis.status_code == 200 and luis.json()["updated_at"] != ana.json()["updated_at"]
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "units_changed",
        "message": "Otra persona cambió las unidades de este cupón. Recarga para ver lo nuevo antes de guardar.",
    }
    assert w.store.get(pid).quotas[0].units == 3
    assert w.client.get(_units_url(pid)).json()["updated_at"] == luis.json()["updated_at"]
    assert [e.action for e in w.audit.entries()].count("units") == 2  # el rechazo no se audita


def test_units_without_expected_updated_at_keep_working_for_old_clients(monkeypatch) -> None:
    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]
    w.client.put(_units_url(pid), json={"rows": [{**_ROW, "units": 5}]})

    res = w.client.put(_units_url(pid), json={"rows": [{**_ROW, "units": 2}]})

    assert res.status_code == 200
    assert w.store.get(pid).quotas[0].units == 2


# A10: cupo ilegible.


def test_saving_units_over_an_unreadable_sheet_is_409_with_a_clear_message(monkeypatch) -> None:
    from src.sdk.connectorkit import QuotaStoreError

    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]

    def unreadable(*a, **k):
        raise QuotaStoreError("roto")

    monkeypatch.setattr(w.store, "replace", unreadable)

    res = w.client.put(_units_url(pid), json={"rows": [{**_ROW, "units": 5}]})

    assert res.status_code == 409
    assert "no guardé nada" in res.json()["detail"]["message"]
    assert w.audit.entries()[0].action == "create"  # nada de "units"


# A7: la lista cuenta cada cupón desde su propio inicio, en UNA lectura.


def test_list_counts_each_coupon_from_its_own_counting_since_in_one_scan(monkeypatch) -> None:
    from src.sdk.connectorkit import PromoUnitQuota

    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]
    # Cupo guardado el 23; la fila se borra el 24 y se vuelve a crear el 25:
    # las ventas del 24 SIGUEN contando (desde cuándo se cuenta no avanza).
    _at(monkeypatch, "2026-09-23T17:00:00Z")
    w.client.put(_units_url(pid), json={"rows": [{**_ROW, "units": 5}]})
    _at(monkeypatch, "2026-09-24T09:00:00Z")
    w.client.put(_units_url(pid), json={"rows": []})
    _at(monkeypatch, "2026-09-25T09:00:00Z")
    w.client.put(_units_url(pid), json={"rows": [{**_ROW, "units": 5}]})
    quota_id = w.store.get(pid).quotas[0].id
    sold_on_24 = _order_with_quota(quota_id, 3, code="AMOR27") | {"created_at": "2026-09-24T12:00:00Z"}
    w.sales.orders = [sold_on_24]
    # Hoja de un cupón que ya no existe en Medusa (vieja): ni se cuenta ni
    # arrastra la lectura hasta 2020.
    ghost = PromoUnitQuota(
        id="q_ghost", promotion_id="promo_gone", code="VIEJO", product_id="prod_cubo", handle="cubo-love",
        title="Cubo Love", color="Rosado", aroma="Café", units=9, created_at="2020-01-01T00:00:00Z",
    )
    w.store.replace("promo_gone", "VIEJO", [ghost], show_units_left=True, actor="ana",
                    now_iso="2020-01-01T00:00:00Z")

    w.sales.scans.clear()
    [listed] = w.client.get("/api/marketing/coupons").json()["coupons"]
    detail = w.client.get(f"/api/marketing/coupons/{pid}").json()["coupon"]

    assert listed["units"] == {"total": 5, "left": 2} == detail["units"]
    assert len(w.sales.scans) == 2  # una de la lista + una del detalle
    assert w.sales.scans[0] >= datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)


# A9 / A14: validar solo lo que cambia; fechas acotadas.


def test_patch_of_a_medusa_coupon_with_a_long_code_edits_the_rest(monkeypatch) -> None:
    w = _world(monkeypatch, seed=[_manageable_seed("promo_long", "AMORYAMISTAD2026")])

    res = w.client.patch("/api/marketing/coupons/promo_long", json={"percentage": 15})

    assert res.status_code == 200, res.text
    assert (res.json()["code"], res.json()["percentage"]) == ("AMORYAMISTAD2026", 15)


@pytest.mark.parametrize("method", ["post", "patch"])
def test_a_far_future_end_date_is_a_422_on_that_field_not_a_500(monkeypatch, method) -> None:
    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]

    if method == "post":
        res = w.client.post("/api/marketing/coupons", json={**_BODY, "code": "OTRO27", "ends_on": "9999-12-31"})
    else:
        res = w.client.patch(f"/api/marketing/coupons/{pid}", json={"ends_on": "9999-12-31"})

    assert res.status_code == 422
    assert res.json()["detail"]["field"] == "ends_on"


# A3: renombrar un borrador.


def test_renaming_a_draft_to_a_code_taken_in_other_case_is_409_without_partial_audit(monkeypatch) -> None:
    lower = _manageable_seed("promo_viejo", "amor26")
    lower["campaign"] = lower["campaign"] | {"campaign_identifier": "AMOR26-VIEJA"}
    w = _world(monkeypatch, seed=[lower])
    draft = _create(w, code="BORRADOR1", status="draft")

    res = w.client.patch(f"/api/marketing/coupons/{draft['promotion_id']}", json={"code": "AMOR26"})

    assert res.status_code == 409
    assert res.json()["detail"]["message"] == "Ese código ya existe."
    assert [e.action for e in w.audit.entries()] == ["create"]


# A4: escrituras que Medusa no confirmó.


def test_create_that_medusa_applied_but_did_not_confirm_is_201_and_audited(monkeypatch) -> None:
    w = _world(monkeypatch)
    real_create = w.admin.medusa.create_promotion

    async def landed_then_timeout(payload):
        await real_create(payload)
        raise TimeoutError("la respuesta no llegó")

    monkeypatch.setattr(w.admin.medusa, "create_promotion", landed_then_timeout)

    res = w.client.post("/api/marketing/coupons", json=_BODY)

    assert res.status_code == 201, res.text
    assert res.json()["code"] == "AMOR27"
    assert [(e.action, e.code) for e in w.audit.entries()] == [("create", "AMOR27")]


def test_create_whose_outcome_is_unknown_says_so_and_never_no_change(monkeypatch) -> None:
    w = _world(monkeypatch)

    async def timeout(payload):
        raise TimeoutError("la respuesta no llegó")

    monkeypatch.setattr(w.admin.medusa, "create_promotion", timeout)

    res = w.client.post("/api/marketing/coupons", json=_BODY)

    assert res.status_code == 503
    message = res.json()["detail"]["message"]
    assert "no se hizo ningún cambio" not in message
    assert "revisa la lista antes de reintentar" in message.lower()


def test_partial_update_whose_state_is_unknown_is_still_audited(monkeypatch) -> None:
    from src.sdk.connectorkit import CouponPartialUpdateError

    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]

    async def half_done(*a, **k):
        raise CouponPartialUpdateError("campaign", "boom", None, applied=("promotion",))

    monkeypatch.setattr(w.admin, "update_coupon", half_done)

    res = w.client.patch(f"/api/marketing/coupons/{pid}", json={"percentage": 15, "ends_on": "2026-09-30"})

    assert res.status_code == 502
    assert (res.json()["detail"]["step"], res.json()["detail"]["coupon"]) == ("campaign", None)
    entry = w.audit.entries()[0]
    assert (entry.action, entry.promotion_id, entry.code) == ("update_partial", pid, "AMOR27")
    # Mismo formato plano que una edición ({campo: [antes, pedido]}), para
    # que el registro del dashboard lo muestre igual.
    assert entry.detail == {
        "percentage": [10, 15],
        "ends_on": ["2026-09-27", "2026-09-30"],
        "failed_step": "campaign",
        "applied_steps": "promotion",
        "state_unknown": True,
    }


def test_update_whose_first_step_medusa_did_not_confirm_is_unknown_not_no_change(monkeypatch) -> None:
    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]

    async def timeout(*a, **k):
        raise TimeoutError("la respuesta no llegó")

    monkeypatch.setattr(w.admin.medusa, "update_promotion", timeout)

    res = w.client.patch(f"/api/marketing/coupons/{pid}", json={"percentage": 15})

    assert res.status_code == 503
    assert "no se hizo ningún cambio" not in res.json()["detail"]["message"]
    assert "reintentar es seguro" in res.json()["detail"]["message"]
    entry = w.audit.entries()[0]
    assert (entry.action, entry.detail) == ("update_unconfirmed", {"percentage": [10, 15]})


def test_status_and_delete_that_medusa_did_not_confirm_are_unknown_not_no_change(monkeypatch) -> None:
    w = _world(monkeypatch)
    active = _create(w)["promotion_id"]
    draft = _create(w, code="BORRADOR1", status="draft")["promotion_id"]

    async def timeout(*a, **k):
        raise TimeoutError("la respuesta no llegó")

    monkeypatch.setattr(w.admin.medusa, "update_promotion", timeout)
    monkeypatch.setattr(w.admin.medusa, "delete_promotion", timeout)

    status = w.client.post(f"/api/marketing/coupons/{active}/status", json={"status": "inactive"})
    deleted = w.client.delete(f"/api/marketing/coupons/{draft}")

    for res in (status, deleted):
        assert res.status_code == 503
        assert "no se hizo ningún cambio" not in res.json()["detail"]["message"]
    assert {e.action for e in w.audit.entries()} >= {"set_status_unconfirmed", "delete_unconfirmed"}


# A8: borrados a medias y campañas que quedan.


def test_delete_that_leaves_the_campaign_behind_audits_its_id(monkeypatch) -> None:
    w = _world(monkeypatch)
    draft = _create(w, code="BORRADOR1", status="draft")

    async def campaign_down(campaign_id):
        raise TimeoutError("no respondió")

    monkeypatch.setattr(w.admin.medusa, "delete_campaign", campaign_down)

    res = w.client.delete(f"/api/marketing/coupons/{draft['promotion_id']}")

    assert res.status_code == 204
    entry = w.audit.entries()[0]
    assert entry.action == "delete"
    assert entry.detail["orphaned_campaign_id"] == draft["campaign_id"]


def test_delete_retry_of_a_coupon_already_gone_cleans_units_and_its_orphan_campaign(monkeypatch) -> None:
    w = _world(monkeypatch)
    draft = _create(w, code="BORRADOR1", status="draft")
    pid = draft["promotion_id"]
    w.client.put(_units_url(pid), json={"rows": [{**_ROW, "units": 2}]})
    # El primer borrado se aplicó en Medusa pero la respuesta no llegó: la
    # promoción ya no existe y su campaña quedó (bloquea el código).
    await_(w.admin.medusa.delete_promotion(pid))

    res = w.client.delete(f"/api/marketing/coupons/{pid}")
    again = w.client.post("/api/marketing/coupons", json={**_BODY, "code": "BORRADOR1", "status": "draft"})

    assert res.status_code == 404
    assert w.store.get(pid).quotas == ()
    entry = w.audit.entries()[1]  # lo más nuevo después del "create" de again
    assert (entry.action, entry.promotion_id) == ("delete", pid)
    assert entry.detail["already_deleted"] is True
    assert entry.detail["orphan_campaigns_deleted"] == [draft["campaign_id"]]
    assert again.status_code == 201, again.text


def test_delete_of_an_unknown_coupon_is_a_plain_404_without_audit(monkeypatch) -> None:
    w = _world(monkeypatch)

    res = w.client.delete("/api/marketing/coupons/promo_nope")

    assert res.status_code == 404
    assert w.audit.entries() == []


def test_create_blocked_by_a_leftover_campaign_says_what_to_do(monkeypatch) -> None:
    w = _world(monkeypatch)
    w.admin.medusa.add_campaign({"id": "procamp_x", "name": "vieja", "campaign_identifier": "AMOR27"})

    res = w.client.post("/api/marketing/coupons", json=_BODY)

    assert res.status_code == 409
    message = res.json()["detail"]["message"]
    assert "campaña" in message and "Medusa Admin" in message and "AMOR27" in message


# A13: errores de dominio que eran 500.


def test_delete_of_a_coupon_that_vanished_meanwhile_is_404_not_500(monkeypatch) -> None:
    from src.sdk.connectorkit import CouponNotFoundError

    w = _world(monkeypatch)
    pid = _create(w, code="BORRADOR1", status="draft")["promotion_id"]

    async def gone(*a, **k):
        raise CouponNotFoundError("borrado")

    monkeypatch.setattr(w.admin, "delete_coupon", gone)

    assert w.client.delete(f"/api/marketing/coupons/{pid}").status_code == 404


def test_medusa_rejecting_a_status_change_or_a_delete_is_422_not_500(monkeypatch) -> None:
    from src.sdk.connectorkit import CouponRejectedError

    w = _world(monkeypatch)
    pid = _create(w, code="BORRADOR1", status="draft")["promotion_id"]

    async def rejected(*a, **k):
        raise CouponRejectedError("no se puede")

    monkeypatch.setattr(w.admin, "set_status", rejected)
    monkeypatch.setattr(w.admin, "delete_coupon", rejected)

    assert w.client.post(f"/api/marketing/coupons/{pid}/status", json={"status": "active"}).status_code == 422
    assert w.client.delete(f"/api/marketing/coupons/{pid}").status_code == 422


def test_vault_failure_after_medusa_deleted_the_coupon_does_not_turn_into_500(monkeypatch) -> None:
    w = _world(monkeypatch)
    pid = _create(w, code="BORRADOR1", status="draft")["promotion_id"]

    def disk_full(*a, **k):
        raise OSError("no space left on device")

    monkeypatch.setattr(w.store, "delete", disk_full)

    res = w.client.delete(f"/api/marketing/coupons/{pid}")

    assert res.status_code == 204
    assert w.client.get(f"/api/marketing/coupons/{pid}").status_code == 404


# A15: sacar un producto del cupón saca sus filas del cupo.


def test_removing_a_product_from_the_coupon_prunes_its_units_and_audits_it(monkeypatch) -> None:
    w = _world(monkeypatch)
    pid = _create(w, products=["prod_cubo", "prod_vaso"])["promotion_id"]
    w.client.put(_units_url(pid), json={"rows": [
        {**_ROW, "units": 5}, {"product_id": "prod_vaso", "color": "Blanco", "units": 4},
    ]})

    res = w.client.patch(f"/api/marketing/coupons/{pid}", json={"products": ["prod_cubo"]})

    assert res.status_code == 200, res.text
    assert [q.product_id for q in w.store.get(pid).quotas] == ["prod_cubo"]
    [listed] = w.client.get("/api/marketing/coupons").json()["coupons"]
    assert listed["units"] == {"total": 5, "left": 5}
    pruned = next(e for e in w.audit.entries() if e.action == "units_pruned")
    assert pruned.detail["rows"] == ["Vaso · Blanco · —: 4"]


def test_two_unit_saves_in_the_same_instant_get_different_versions_and_the_older_is_409(monkeypatch) -> None:
    """C-5 con el reloj quieto (`now` fijo): la versión igual tiene que cambiar."""
    w = _world(monkeypatch)
    pid = _create(w)["promotion_id"]

    first = w.client.put(_units_url(pid), json={"rows": [{**_ROW, "units": 5}], "expected_updated_at": None})
    second = w.client.put(
        _units_url(pid), json={"rows": [{**_ROW, "units": 3}], "expected_updated_at": first.json()["updated_at"]}
    )
    stale = w.client.put(
        _units_url(pid), json={"rows": [{**_ROW, "units": 8}], "expected_updated_at": first.json()["updated_at"]}
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["updated_at"] != second.json()["updated_at"]
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "units_changed"
    assert w.store.get(pid).quotas[0].units == 3
