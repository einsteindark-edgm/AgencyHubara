"""Contract suite del `PromotionsAdminPort` (Fase 2 de CUPONES_PLAN.md).

La misma suite corre contra el doble oficial (`FakePromotionsAdmin`, Medusa
en memoria) y contra el adapter real (`MedusaPromotionsAdmin` +
`HttpMedusaClient`) con `respx` delante del MISMO simulador: así se prueba el
cableado HTTP (rutas, sobres, errores) con la semántica de Medusa. Nunca toca
una Medusa real.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

import httpx
import pytest
import respx

from src.platform.medusa.client import HttpMedusaClient, MedusaAPIError
from src.platform.promotions.admin import (
    CouponCodeTakenError,
    CouponDeleteRefusedError,
    CouponNotFoundError,
    CouponNotManageableError,
    CouponPartialUpdateError,
    CouponRejectedError,
    FakePromotionsAdmin,
    InMemoryMedusaPromotions,
    MedusaPromotionsAdmin,
    PromotionsAdminPort,
)
from src.platform.promotions.coupon import CouponSpec
from src.platform.promotions.medusa import MedusaPromotionsPort
from src.platform.promotions.port import PromotionsUnavailableError

_BASE = "http://medusa.test"
_NOW = datetime(2026, 9, 23, 17, 0, tzinfo=timezone.utc)


def _clock() -> datetime:
    return _NOW


def _spec(**overrides: Any) -> CouponSpec:
    base: dict[str, Any] = dict(
        code="AMOR27",
        campaign_name="AMOR Y AMISTAD 2026",
        percentage=10,
        products=("prod_a", "prod_b"),
        starts_on=date(2026, 9, 22),
        ends_on=date(2026, 9, 27),
        status="active",
    )
    base.update(overrides)
    return CouponSpec(**base)


def _tagged_amor26() -> dict[str, Any]:
    """AMOR26 como está en prod: productos + condición por etiquetas."""
    return {
        "id": "promo_amor26",
        "code": "AMOR26",
        "type": "standard",
        "is_automatic": False,
        "status": "active",
        "campaign_id": "camp_amor26",
        "application_method": {
            "id": "proappmet_amor26",
            "type": "percentage",
            "value": 10,
            "target_type": "items",
            "allocation": "each",
            "max_quantity": 10,
            "target_rules": [
                {"id": "prorul_p", "attribute": "items.product.id", "operator": "in", "values": [{"value": "prod_a"}]},
                {"id": "prorul_t", "attribute": "items.product.tags.id", "operator": "in", "values": [{"value": "ptag_1"}]},
            ],
        },
        "rules": [],
        "campaign": {
            "id": "camp_amor26",
            "name": "AMOR Y AMISTAD 2026",
            "campaign_identifier": "AMOR26",
            "starts_at": "2026-09-22T05:00:00.000Z",
            "ends_at": "2026-09-27T05:00:00.000Z",
            "budget": None,
        },
    }


# ---------------------------------------------------------------------------
# respx delante del simulador en memoria: HTTP real → semántica de Medusa.
# ---------------------------------------------------------------------------

_PROMO = re.compile(r"^/admin/promotions/([^/]+)$")
_BATCH = re.compile(r"^/admin/promotions/([^/]+)/target-rules/batch$")
_CAMPAIGN = re.compile(r"^/admin/campaigns/([^/]+)$")


def _medusa_http(sim: InMemoryMedusaPromotions):
    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        try:
            if request.method == "GET" and path == "/admin/promotions":
                promos = await sim.list_promotions()
                return httpx.Response(200, json={"promotions": promos, "count": len(promos), "offset": 0, "limit": 100})
            if request.method == "POST" and path == "/admin/promotions":
                return httpx.Response(200, json={"promotion": await sim.create_promotion(body)})
            if m := _BATCH.match(path):
                return httpx.Response(200, json=await sim.batch_promotion_target_rules(m[1], **body))
            if m := _PROMO.match(path):
                if request.method == "GET":
                    return httpx.Response(200, json={"promotion": await sim.get_promotion(m[1])})
                if request.method == "POST":
                    return httpx.Response(200, json={"promotion": await sim.update_promotion(m[1], body)})
                if request.method == "DELETE":
                    return httpx.Response(200, json=await sim.delete_promotion(m[1]))
            if m := _CAMPAIGN.match(path):
                if request.method == "POST":
                    return httpx.Response(200, json={"campaign": await sim.update_campaign(m[1], body)})
                if request.method == "DELETE":
                    return httpx.Response(200, json=await sim.delete_campaign(m[1]))
        except MedusaAPIError as exc:
            return httpx.Response(exc.status_code, text=exc.body)
        return httpx.Response(404, json={"type": "not_found", "message": f"no route {request.method} {path}"})

    return handler


def _port(kind: str, mock: respx.MockRouter, seed: list[dict[str, Any]] | None = None) -> PromotionsAdminPort:
    if kind == "fake":
        return FakePromotionsAdmin(seed, clock=_clock)
    sim = InMemoryMedusaPromotions(seed)
    mock.route(host="medusa.test").mock(side_effect=_medusa_http(sim))
    client = HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0)
    return MedusaPromotionsAdmin(client, clock=_clock)


KINDS = pytest.mark.parametrize("kind", ["fake", "real"])


@pytest.mark.asyncio
@KINDS
async def test_create_coupon_returns_view_and_lists_it(kind: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _port(kind, mock)
        view = await port.create_coupon(_spec())
        listed = await port.list_coupons()

    assert view.code == "AMOR27"
    assert view.percentage == 10
    assert view.products == ("prod_a", "prod_b")
    assert view.starts_on == date(2026, 9, 22)
    assert view.ends_on == date(2026, 9, 27)
    assert view.state == "active"
    assert view.manageable is True
    assert [v.code for v in listed] == ["AMOR27"]


@pytest.mark.asyncio
async def test_create_coupon_posts_one_request_with_inline_campaign() -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _port("real", mock)
        await port.create_coupon(_spec())
        writes = [c.request for c in mock.calls if c.request.method != "GET"]

    # UNA escritura: promoción + campaña juntas (nada queda a medias si falla).
    assert [(r.method, r.url.path) for r in writes] == [("POST", "/admin/promotions")]
    body = json.loads(writes[0].content)
    assert body["campaign"]["campaign_identifier"] == "AMOR27"
    assert "campaign_id" not in body


@pytest.mark.asyncio
@KINDS
async def test_create_coupon_with_taken_code_raises_coupon_code_taken(kind: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _port(kind, mock, [_tagged_amor26()])
        with pytest.raises(CouponCodeTakenError):
            await port.create_coupon(_spec(code="AMOR26"))
        assert [v.code for v in await port.list_coupons()] == ["AMOR26"]


@pytest.mark.asyncio
@KINDS
async def test_create_coupon_code_taken_ignores_case(kind: str) -> None:
    # Medusa distingue mayúsculas, pero el bot no: "amor26" y "AMOR26" chocan.
    lower = _tagged_amor26() | {"code": "amor26"}
    lower["campaign"] = lower["campaign"] | {"campaign_identifier": "AMOR26-VIEJA"}
    with respx.mock(assert_all_called=False) as mock:
        port = _port(kind, mock, [lower])
        with pytest.raises(CouponCodeTakenError):
            await port.create_coupon(_spec(code="AMOR26"))


@pytest.mark.asyncio
async def test_create_coupon_invalid_data_raises_coupon_rejected_with_message() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{_BASE}/admin/promotions").mock(
            return_value=httpx.Response(200, json={"promotions": [], "count": 0, "offset": 0, "limit": 100})
        )
        mock.post(f"{_BASE}/admin/promotions").mock(
            return_value=httpx.Response(
                400, json={"type": "invalid_data", "message": "Application Method value should be a percentage (0-100)"}
            )
        )
        client = HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0)
        port = MedusaPromotionsAdmin(client, clock=_clock)
        with pytest.raises(CouponRejectedError) as err:
            await port.create_coupon(_spec())

    assert "percentage (0-100)" in err.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [httpx.Response(503, json={"message": "down"}), httpx.ConnectError("refused")],
)
async def test_medusa_unreachable_raises_promotions_unavailable(failure) -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route(host="medusa.test")
        if isinstance(failure, Exception):
            route.mock(side_effect=failure)
        else:
            route.mock(return_value=failure)
        client = HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0)
        port = MedusaPromotionsAdmin(client, clock=_clock)
        with pytest.raises(PromotionsUnavailableError):
            await port.list_coupons()
        with pytest.raises(PromotionsUnavailableError):
            await port.create_coupon(_spec())


# ---------------------------------------------------------------------------
# Editar, pausar, borrar.
# ---------------------------------------------------------------------------


def _writes(mock: respx.MockRouter) -> list[tuple[str, str, Any]]:
    return [
        (c.request.method, c.request.url.path, json.loads(c.request.content) if c.request.content else None)
        for c in mock.calls
        if c.request.method != "GET"
    ]


@pytest.mark.asyncio
@KINDS
async def test_update_changes_every_form_field(kind: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _port(kind, mock)
        created = await port.create_coupon(_spec())
        view = await port.update_coupon(
            created.promotion_id,
            _spec(
                campaign_name="AMOR 2026",
                percentage=15,
                products=("prod_c",),
                starts_on=date(2026, 9, 24),
                ends_on=date(2026, 9, 30),
            ),
        )

    assert (view.campaign_name, view.percentage, view.products) == ("AMOR 2026", 15, ("prod_c",))
    assert (view.starts_on, view.ends_on) == (date(2026, 9, 24), date(2026, 9, 30))


@pytest.mark.asyncio
async def test_update_sends_only_changed_fields() -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _port("real", mock)
        created = await port.create_coupon(_spec())
        mock.reset()
        await port.update_coupon(created.promotion_id, _spec(percentage=15))
        only_pct = _writes(mock)
        mock.reset()
        await port.update_coupon(created.promotion_id, _spec(percentage=15, ends_on=date(2026, 9, 29)))
        only_dates = _writes(mock)

    assert only_pct == [
        ("POST", f"/admin/promotions/{created.promotion_id}", {"application_method": {"value": 15}})
    ]
    assert only_dates == [
        ("POST", f"/admin/campaigns/{created.campaign_id}", {"ends_at": "2026-09-30T05:00:00Z"})
    ]


@pytest.mark.asyncio
async def test_update_products_batches_only_the_product_rule() -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _port("real", mock)
        created = await port.create_coupon(_spec())
        rule_id = (await port.raw_promotion(created.promotion_id))["application_method"]["target_rules"][0]["id"]
        mock.reset()
        await port.update_coupon(created.promotion_id, _spec(products=("prod_c",)))
        changed = _writes(mock)
        mock.reset()
        await port.update_coupon(created.promotion_id, _spec(products=None))
        to_all = _writes(mock)
        mock.reset()
        view = await port.update_coupon(created.promotion_id, _spec(products=("prod_a",)))
        from_all = _writes(mock)

    batch = f"/admin/promotions/{created.promotion_id}/target-rules/batch"
    assert changed == [
        ("POST", batch, {"create": [], "update": [{"id": rule_id, "operator": "in", "values": ["prod_c"]}], "delete": []})
    ]
    assert to_all == [("POST", batch, {"create": [], "update": [], "delete": [rule_id]})]
    assert from_all == [
        ("POST", batch, {"create": [{"attribute": "items.product.id", "operator": "in", "values": ["prod_a"]}], "update": [], "delete": []})
    ]
    assert view.products == ("prod_a",)


@pytest.mark.asyncio
@KINDS
async def test_update_refuses_unmanageable_promotion(kind: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _port(kind, mock, [_tagged_amor26()])
        with pytest.raises(CouponNotManageableError) as err:
            await port.update_coupon("promo_amor26", _spec(code="AMOR26", percentage=20))
        with pytest.raises(CouponNotManageableError):
            await port.set_status("promo_amor26", "inactive")
        view = await port.get_coupon("promo_amor26")

    assert "etiquetas" in err.value.reason
    assert view.percentage == 10 and view.status == "active"
    if kind == "fake":
        assert port.medusa.writes == []
    else:
        assert _writes(mock) == []


@pytest.mark.asyncio
@KINDS
async def test_update_code_only_while_draft(kind: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _port(kind, mock)
        active = await port.create_coupon(_spec())
        with pytest.raises(CouponRejectedError) as err:
            await port.update_coupon(active.promotion_id, _spec(code="AMOR28"))
        draft = await port.create_coupon(_spec(code="BORRADOR1", status="draft"))
        renamed = await port.update_coupon(draft.promotion_id, _spec(code="BORRADOR2", status="draft"))

    assert "borrador" in err.value.message
    assert renamed.code == "BORRADOR2"


@pytest.mark.asyncio
@KINDS
async def test_set_status_pauses_and_resumes(kind: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _port(kind, mock)
        created = await port.create_coupon(_spec())
        paused = await port.set_status(created.promotion_id, "inactive")
        resumed = await port.set_status(created.promotion_id, "active")

    assert (paused.state, resumed.state) == ("paused", "active")


@pytest.mark.asyncio
@KINDS
async def test_delete_refused_unless_draft(kind: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _port(kind, mock)
        active = await port.create_coupon(_spec())
        with pytest.raises(CouponDeleteRefusedError):
            await port.delete_coupon(active.promotion_id)
        draft = await port.create_coupon(_spec(code="BORRADOR1", status="draft"))
        await port.delete_coupon(draft.promotion_id)
        codes = [v.code for v in await port.list_coupons()]
        with pytest.raises(CouponNotFoundError):
            await port.get_coupon(draft.promotion_id)

    assert codes == ["AMOR27"]


@pytest.mark.asyncio
async def test_delete_draft_removes_promotion_and_its_campaign() -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _port("real", mock)
        draft = await port.create_coupon(_spec(status="draft"))
        mock.reset()
        await port.delete_coupon(draft.promotion_id)
        deletes = [(m, p) for m, p, _ in _writes(mock)]
        # El código y el identificador de campaña quedan libres otra vez.
        again = await port.create_coupon(_spec(status="draft"))

    assert deletes == [
        ("DELETE", f"/admin/promotions/{draft.promotion_id}"),
        ("DELETE", f"/admin/campaigns/{draft.campaign_id}"),
    ]
    assert again.code == "AMOR27"


@pytest.mark.asyncio
async def test_partial_update_reports_failed_step_with_reread_state() -> None:
    sim = InMemoryMedusaPromotions()
    medusa = _medusa_http(sim)

    async def campaign_down(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.startswith("/admin/campaigns/"):
            return httpx.Response(500, json={"type": "unknown_error", "message": "boom"})
        return await medusa(request)

    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="medusa.test").mock(side_effect=campaign_down)
        client = HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0)
        port = MedusaPromotionsAdmin(client, clock=_clock)
        created = await port.create_coupon(_spec())
        # La campaña falla después de que la promoción ya cambió.
        with pytest.raises(CouponPartialUpdateError) as err:
            await port.update_coupon(created.promotion_id, _spec(percentage=15, ends_on=date(2026, 9, 29)))

    assert err.value.step == "campaign"
    assert err.value.view is not None
    assert err.value.view.percentage == 15  # lo que SÍ quedó en Medusa
    assert err.value.view.ends_on == date(2026, 9, 27)


@pytest.mark.asyncio
@KINDS
async def test_unknown_promotion_raises_not_found(kind: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _port(kind, mock)
        with pytest.raises(CouponNotFoundError):
            await port.get_coupon("promo_nope")
        with pytest.raises(CouponNotFoundError):
            await port.set_status("promo_nope", "active")


@pytest.mark.asyncio
async def test_admin_write_clears_local_promotions_cache() -> None:
    with respx.mock(assert_all_called=False) as mock:
        sim = InMemoryMedusaPromotions()
        mock.route(host="medusa.test").mock(side_effect=_medusa_http(sim))
        client = HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0)
        reader = MedusaPromotionsPort(client, ttl_s=3600)
        admin = MedusaPromotionsAdmin(client, clock=_clock, on_change=reader.invalidate)

        assert await reader.get_by_code("AMOR27") is None  # queda en cache
        await admin.create_coupon(_spec())
        seen = await reader.get_by_code("AMOR27")

    assert seen is not None and seen.value == 10



@pytest.mark.asyncio
async def test_writes_are_not_retried_after_a_read_timeout() -> None:
    """Un POST que se cortó por timeout PUDO haber creado la promoción:
    reintentarlo daría "ya existe" sobre el cupón recién creado. Las
    escrituras solo se reintentan si la conexión ni siquiera se abrió."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{_BASE}/admin/promotions").mock(
            return_value=httpx.Response(200, json={"promotions": [], "count": 0, "offset": 0, "limit": 100})
        )
        post = mock.post(f"{_BASE}/admin/promotions").mock(side_effect=httpx.ReadTimeout("slow"))
        client = HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0)
        port = MedusaPromotionsAdmin(client, clock=_clock)
        with pytest.raises(PromotionsUnavailableError):
            await port.create_coupon(_spec())

    assert post.call_count == 1


@pytest.mark.asyncio
async def test_rename_whose_campaign_step_hits_a_taken_identifier_is_a_partial_update() -> None:
    orphan = {"id": "procamp_x", "name": "vieja", "campaign_identifier": "BORRADOR2"}
    sim = InMemoryMedusaPromotions()
    sim._orphan_campaigns["procamp_x"] = orphan
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="medusa.test").mock(side_effect=_medusa_http(sim))
        client = HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0)
        port = MedusaPromotionsAdmin(client, clock=_clock)
        draft = await port.create_coupon(_spec(code="BORRADOR1", status="draft"))
        with pytest.raises(CouponPartialUpdateError) as err:
            await port.update_coupon(draft.promotion_id, _spec(code="BORRADOR2", status="draft"))

    assert err.value.step == "campaign"
    assert err.value.view is not None and err.value.view.code == "BORRADOR2"


@pytest.mark.asyncio
async def test_created_coupon_is_returned_even_if_the_reread_fails() -> None:
    """La promoción ya existe en Medusa: responder "no se hizo ningún cambio"
    mentiría (y dejaría el cambio sin registro)."""
    sim = InMemoryMedusaPromotions()
    medusa = _medusa_http(sim)

    async def reread_down(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.startswith("/admin/promotions/"):
            return httpx.Response(503, json={"message": "down"})
        return await medusa(request)

    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="medusa.test").mock(side_effect=reread_down)
        client = HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0)
        port = MedusaPromotionsAdmin(client, clock=_clock)
        view = await port.create_coupon(_spec())

    assert view.code == "AMOR27" and view.percentage == 10
