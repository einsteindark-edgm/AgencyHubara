"""Contract suite del `PromotionsPort` — parametrizada fake / real (respx).

Regla de oro del connectorkit: ningún port sin fake, ningún adapter sin
suite. Ambas implementaciones deben comportarse igual ante el mismo estado.
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from src.platform.medusa.client import HttpMedusaClient
from src.platform.promotions.medusa import MedusaPromotionsPort
from src.platform.promotions.port import (
    FakePromotionsPort,
    PromotionsPort,
    PromotionsUnavailableError,
)
from src.platform.promotions.rules import resolve_coupon

_BASE = "http://medusa.test"

_RAW = [
    {
        "id": "promo_1",
        "code": "MAMA15",
        "type": "standard",
        "is_automatic": False,
        "status": "active",
        "application_method": {"type": "percentage", "value": 15, "target_type": "items", "allocation": "across"},
        "rules": [],
        "campaign": None,
    },
    {
        "id": "promo_2",
        "code": "VIEJO",
        "type": "standard",
        "is_automatic": False,
        "status": "inactive",
        "application_method": {"type": "fixed", "value": 5000, "currency_code": "cop", "target_type": "order", "allocation": "across"},
        "rules": [],
        "campaign": None,
    },
    {
        "id": "promo_3",
        "code": "AUTO",
        "type": "standard",
        "is_automatic": True,
        "status": "active",
        "application_method": {"type": "percentage", "value": 5, "target_type": "items", "allocation": "across"},
        "rules": [],
        "campaign": None,
    },
]


def _fake() -> PromotionsPort:
    from src.platform.promotions.medusa import promotion_from_medusa

    return FakePromotionsPort([p for p in (promotion_from_medusa(r) for r in _RAW) if p])


async def _real(mock) -> PromotionsPort:
    mock.get(f"{_BASE}/admin/promotions").mock(
        return_value=Response(200, json={"promotions": _RAW, "count": 3, "offset": 0, "limit": 100})
    )
    client = HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0)
    return MedusaPromotionsPort(client, ttl_s=60)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["fake", "real"])
async def test_list_active_excludes_inactive_and_automatic(kind: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _fake() if kind == "fake" else await _real(mock)
        active = await port.list_active()
        codes = sorted(p.code for p in active)
    if kind == "fake":
        # El fake solo filtra status (sirve para probar promos automáticas).
        assert codes == ["AUTO", "MAMA15"]
    else:
        assert codes == ["MAMA15"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["fake", "real"])
async def test_get_by_code_is_case_insensitive_and_finds_inactive(kind: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _fake() if kind == "fake" else await _real(mock)
        found = await port.get_by_code("mama15")
        old = await port.get_by_code("VIEJO")
        missing = await port.get_by_code("NADA")
    assert found is not None and found.code == "MAMA15"
    assert old is not None and old.status == "inactive"
    assert missing is None
    # Con el mismo estado, la resolución da lo mismo en ambos.
    assert resolve_coupon("VIEJO", [old], now_ms=1).reason == "inactive"


@pytest.mark.asyncio
async def test_real_port_caches_and_reports_unavailable() -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{_BASE}/admin/promotions").mock(
            return_value=Response(200, json={"promotions": _RAW, "count": 3, "offset": 0, "limit": 100})
        )
        client = HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0)
        port = MedusaPromotionsPort(client, ttl_s=60)
        await port.list_active()
        await port.get_by_code("MAMA15")
        assert route.call_count == 1  # cache por TTL

        down = MedusaPromotionsPort(
            HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0), ttl_s=60
        )
        route.mock(return_value=Response(503, json={"message": "down"}))
        with pytest.raises(PromotionsUnavailableError):
            await down.list_active()


# --- Condición por etiquetas (run 28a8e407) -----------------------------------
# La regla trae ids de etiqueta; el adapter los traduce al nombre ("Color:
# Rosado") que tiene el catálogo. Si Medusa no responde esa lectura, la promo
# queda con alcance desconocido (falla cerrada), el resto se lee igual.

_TAGGED = {
    "id": "promo_9",
    "code": "AMOR26",
    "type": "standard",
    "is_automatic": False,
    "status": "active",
    "application_method": {
        "type": "percentage",
        "value": 10,
        "target_type": "items",
        "allocation": "each",
        "target_rules": [
            {"attribute": "items.product.id", "operator": "in", "values": [{"value": "prod_a"}]},
            {
                "attribute": "items.product.tags.id",
                "operator": "in",
                "values": [{"value": "ptag_rosado"}, {"value": "ptag_cafe"}],
            },
        ],
    },
    "rules": [],
    "campaign": None,
}


def _tagged_port(mock, tags_response: Response) -> MedusaPromotionsPort:
    mock.get(f"{_BASE}/admin/promotions").mock(
        return_value=Response(
            200, json={"promotions": [*_RAW, _TAGGED], "count": 4, "offset": 0, "limit": 100}
        )
    )
    mock.get(f"{_BASE}/admin/product-tags").mock(return_value=tags_response)
    client = HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0)
    return MedusaPromotionsPort(client, ttl_s=60)


@pytest.mark.asyncio
async def test_real_adapter_translates_tag_ids_to_tag_names() -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _tagged_port(
            mock,
            Response(
                200,
                json={
                    "product_tags": [
                        {"id": "ptag_rosado", "value": "Color: Rosado"},
                        {"id": "ptag_cafe", "value": "Aroma: Café"},
                    ],
                    "count": 2,
                    "offset": 0,
                    "limit": 100,
                },
            ),
        )
        promo = await port.get_by_code("AMOR26")
    assert promo is not None
    assert promo.tag_values == ("Color: Rosado", "Aroma: Café")
    assert promo.scope_unresolved is False


@pytest.mark.asyncio
async def test_tags_that_cannot_be_read_fail_closed_only_for_that_promo() -> None:
    with respx.mock(assert_all_called=False) as mock:
        port = _tagged_port(mock, Response(500, json={"message": "boom"}))
        promo = await port.get_by_code("AMOR26")
        other = await port.get_by_code("MAMA15")
    assert promo is not None and promo.scope_unresolved is True
    assert other is not None and other.scope_unresolved is False
