"""Ventas de un cupón derivadas de los pedidos de Medusa (Fase 4).

Las vendidas de cada cupo NO se guardan: se cuentan de las líneas que llevan
`metadata.coupon_quota_id` (la marca que escribe el pedido con cupo, junto a
las claves de la Fase 0: `coupon_code`, `list_unit_price_cop`,
`discount_unit_cop`). Shapes saneados de `/admin/orders` y
`/admin/draft-orders`: sin teléfonos, direcciones ni emails reales.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
import respx

from src.platform.medusa.client import HttpMedusaClient
from src.platform.promotions.coupon_sales import (
    CouponSalesReader,
    coupon_results,
    quota_board,
    sold_units_by_quota,
)
from src.platform.promotions.quota_store import QuotaSheet
from src.platform.promotions.quotas import PromoUnitQuota
from src.platform.promotions.port import PromotionsUnavailableError

_BASE = "http://medusa.test"
_Q = "q_rosado_cafe"


def _line(qty: int, *, quota: str | None = _Q, code: str | None = "AMOR26", discount: int = 2100,
          price: int = 21000) -> dict[str, Any]:
    meta: dict[str, Any] = {"handle": "cubo-love", "variant_label": "Rosado · Café"}
    if code:
        meta |= {"coupon_code": code, "list_unit_price_cop": price, "discount_unit_cop": discount}
    if quota:
        meta["coupon_quota_id"] = quota
    return {
        "id": f"ordli_{qty}_{quota}",
        "title": "Cubo Love",
        "quantity": qty,
        "unit_price": price - (discount if code else 0),
        "metadata": meta,
    }


def _order(oid: str, items: list[dict[str, Any]], *, status: str = "draft", created: str = "2026-09-23T15:00:00.000Z",
           meta: dict[str, Any] | None = None, canceled_at: str | None = None, display_id: int = 45) -> dict[str, Any]:
    return {
        "id": oid,
        "display_id": display_id,
        "status": status,
        "created_at": created,
        "canceled_at": canceled_at,
        "email": "wa+wa_test@hubara.local",
        "metadata": {"coupon_code": "AMOR26", "discount_cop": 2100, **(meta or {})},
        "items": items,
    }


def test_sold_units_from_medusa_reads_line_metadata() -> None:
    orders = [
        _order("order_1", [_line(2), _line(1, quota=None, code=None)]),
        _order("order_2", [_line(1), _line(3, quota="q_azul_lavanda")], status="pending"),
    ]

    assert sold_units_by_quota(orders) == {_Q: 3, "q_azul_lavanda": 3}


def test_sold_ignores_cancelled_deleted_and_test_orders() -> None:
    orders = [
        _order("order_ok", [_line(1)]),
        _order("order_medusa_cancel", [_line(5)], status="canceled"),
        _order("order_canceled_at", [_line(5)], status="pending", canceled_at="2026-09-23T16:00:00Z"),
        _order("order_soft_cancel", [_line(5)], meta={"hubara_stage": "cancelled"}),
        _order("order_test", [_line(5)], meta={"hubara_test_order": True}),
        # El mismo pedido visto dos veces (draft recién convertido) cuenta una.
        _order("order_ok", [_line(1)], status="pending"),
    ]

    assert sold_units_by_quota(orders) == {_Q: 1}


def test_coupon_results_sum_orders_discount_and_units() -> None:
    orders = [
        _order("order_1", [_line(2), _line(1, quota=None, code=None)], display_id=45),
        # Sin cupo: el cupón aplicó a todo el producto (sin coupon_quota_id).
        _order("order_2", [_line(1, quota=None)], display_id=46),
        # Pedido viejo (antes de la Fase 0): solo metadata del pedido.
        _order("order_legacy", [_line(1, quota=None, code=None)], meta={"discount_cop": 4200}, display_id=44),
        _order("order_other", [_line(1, quota=None, code="OTRO")], meta={"coupon_code": "OTRO"}),
        _order("order_cancel", [_line(9)], status="canceled"),
    ]

    results = coupon_results("AMOR26", orders)

    assert results.orders == 3
    assert results.discount_cop == 2 * 2100 + 2100 + 4200
    assert results.quota_units == 2
    assert [(s.display_id, s.quota_units, s.discount_cop) for s in results.sales] == [
        (45, 2, 4200),
        (46, 0, 2100),
        (44, 0, 4200),
    ]


# ---------------------------------------------------------------------------
# El lector HTTP: pedidos + drafts desde el inicio de la campaña.
# ---------------------------------------------------------------------------


def _page(key: str, rows: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(200, json={key: rows, "count": len(rows), "offset": 0, "limit": 100})


@pytest.mark.asyncio
async def test_reader_merges_orders_and_drafts_since_campaign_start() -> None:
    since = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{_BASE}/admin/orders").mock(return_value=_page("orders", [
            _order("order_new", [_line(1)], status="pending", created="2026-09-23T10:00:00Z"),
            # Anterior a la campaña: no cuenta (y corta la paginación).
            _order("order_old", [_line(4)], status="pending", created="2026-09-20T10:00:00Z"),
        ]))
        mock.get(f"{_BASE}/admin/draft-orders").mock(return_value=_page("draft_orders", [
            _order("order_draft", [_line(2)], created="2026-09-23T12:00:00Z"),
        ]))
        reader = CouponSalesReader(HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0))

        sold = await reader.sold_units(since=since)
        params = mock.calls[0].request.url.params

    assert sold == {_Q: 3}
    assert params["order"] == "-created_at"
    assert "*items" in params["fields"] and "metadata" in params["fields"]


@pytest.mark.asyncio
async def test_reader_pages_until_orders_before_since() -> None:
    since = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)
    first = [_order(f"order_{i}", [_line(1)], status="pending", created="2026-09-23T10:00:00Z") for i in range(2)]
    second = [_order("order_2", [_line(1)], status="pending", created="2026-09-22T06:00:00Z"),
              _order("order_3", [_line(1)], status="pending", created="2026-09-21T06:00:00Z")]

    def orders(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        rows = first if offset == 0 else second if offset == 2 else []
        return httpx.Response(200, json={"orders": rows, "count": 50, "offset": offset, "limit": 2})

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{_BASE}/admin/orders").mock(side_effect=orders)
        mock.get(f"{_BASE}/admin/draft-orders").mock(return_value=_page("draft_orders", []))
        reader = CouponSalesReader(
            HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0), page_size=2
        )
        sold = await reader.sold_units(since=since)

    assert sold == {_Q: 3}
    assert route.call_count == 2  # la página con un pedido anterior corta


@pytest.mark.asyncio
async def test_reader_medusa_down_raises_promotions_unavailable() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="medusa.test").mock(return_value=httpx.Response(503, json={"message": "down"}))
        reader = CouponSalesReader(HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0))
        with pytest.raises(PromotionsUnavailableError):
            await reader.sold_units(since=datetime(2026, 9, 22, tzinfo=timezone.utc))


class _Reader:
    def __init__(self, sold: dict[str, int]) -> None:
        self.sold = sold
        self.since: list[datetime] = []

    async def sold_units(self, *, since: datetime) -> dict[str, int]:
        self.since.append(since)
        return self.sold


def _row(qid: str, units: int, created_at: str) -> PromoUnitQuota:
    return PromoUnitQuota(qid, "promo_1", "AMOR26", "prod_cubo", "cubo-love", "Cubo Love",
                          "Rosado", qid, units, created_at, "ana")


@pytest.mark.asyncio
async def test_quota_board_counts_sales_since_the_first_quota_row() -> None:
    reader = _Reader({"q1": 2})
    sheet = QuotaSheet("promo_1", "AMOR26", (
        _row("q1", 5, "2026-09-23T17:00:00Z"),
        _row("q2", 1, "2026-09-22T10:00:00Z"),
    ))

    board = await quota_board(sheet, reader)

    # Una línea con marca de cupo no puede ser anterior a la fila más vieja
    # (menos una hora de margen por el reloj).
    assert reader.since == [datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)]
    assert [(s.quota.id, s.units_left) for s in board] == [("q1", 3), ("q2", 1)]


@pytest.mark.asyncio
async def test_quota_board_without_rows_reads_nothing() -> None:
    reader = _Reader({})

    assert await quota_board(QuotaSheet("promo_1", "", ()), reader) == []
    assert reader.since == []


@pytest.mark.asyncio
async def test_quota_board_counts_from_counting_since_with_a_clock_margin() -> None:
    reader = _Reader({})
    sheet = QuotaSheet("promo_1", "AMOR26", (_row("q1", 5, "2026-09-25T09:00:00Z"),),
                       counting_since="2026-09-23T17:00:00Z")

    await quota_board(sheet, reader)

    # Una hora de margen por la diferencia de reloj entre el host y Medusa.
    assert reader.since == [datetime(2026, 9, 23, 16, 0, tzinfo=timezone.utc)]


@pytest.mark.asyncio
async def test_reader_without_count_keeps_paging_until_a_short_page() -> None:
    """Sin `count` en la respuesta NO se corta en la primera página (contaría
    menos vendidas y el cupo vendería de más)."""
    since = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)
    pages = {
        0: [_order("order_a", [_line(1)], status="pending", created="2026-09-23T10:00:00Z"),
            _order("order_b", [_line(1)], status="pending", created="2026-09-23T09:00:00Z")],
        2: [_order("order_c", [_line(1)], status="pending", created="2026-09-23T08:00:00Z")],
    }

    def orders(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"orders": pages.get(int(request.url.params["offset"]), [])})

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{_BASE}/admin/orders").mock(side_effect=orders)
        mock.get(f"{_BASE}/admin/draft-orders").mock(return_value=httpx.Response(200, json={"draft_orders": []}))
        reader = CouponSalesReader(HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0), page_size=2)
        sold = await reader.sold_units(since=since)

    assert sold == {_Q: 3}


# --- L-28: el reintento de un pedido no cuenta su propio draft -----------------------


def test_sold_units_leave_out_the_order_being_retried() -> None:
    """Si el intento original SÍ creó el draft (Medusa respondió tarde), ese
    draft es el MISMO pedido que se reintenta: no cuenta como vendido para
    él (misma sesión y mismo fingerprint). Otra sesión con el mismo
    fingerprint es otro cliente y sí cuenta."""
    own = _order("order_own", [_line(1)], meta={"session_key": "wa_a", "order_fingerprint": "fp1"})
    other = _order("order_other", [_line(2)], meta={"session_key": "wa_b", "order_fingerprint": "fp1"})

    assert sold_units_by_quota([own, other], exclude={("wa_a", "fp1")}) == {_Q: 2}


@pytest.mark.asyncio
async def test_reader_and_board_pass_the_excluded_order_down() -> None:
    since = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)
    own = _order("order_own", [_line(1)], created="2026-09-23T12:00:00Z",
                 meta={"session_key": "wa_a", "order_fingerprint": "fp1"})
    with respx.mock() as mock:
        mock.get(f"{_BASE}/admin/orders").mock(return_value=_page("orders", []))
        mock.get(f"{_BASE}/admin/draft-orders").mock(return_value=_page("draft_orders", [own]))
        reader = CouponSalesReader(HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0))
        sheet = QuotaSheet("promo_1", "AMOR26", (_row(_Q, 1, "2026-09-23T10:00:00Z"),))

        counted = await reader.sold_units(since=since)
        board = await quota_board(sheet, reader, exclude={("wa_a", "fp1")})

    assert counted == {_Q: 1}
    assert [(s.quota.id, s.units_left) for s in board] == [(_Q, 1)]
