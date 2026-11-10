"""Ajustar el precio de un pedido al valor real de la venta (precio negociado).

Caso real (pedido #169, 2026-11-09): 20 cajas de velas de deseos vendidas al
por mayor, con el precio negociado con el cliente, quedaron registradas a
precio de lista ($475.000). Al marcarlo entregado infló las ventas. El precio
al por mayor se negocia en cada venta: el operador escribe el valor real por
línea y Medusa queda con ESE precio (todo Hubara lee el total de Medusa).

Puro: decide qué líneas cambian y qué queda en la auditoría.
"""
from __future__ import annotations

import pytest

from src.platform.orders.price_adjustment import (
    PriceAdjustmentError,
    build_price_adjustment_patch,
    plan_line_prices,
)
from src.platform.orders.state import META_KEY_PRICE_ADJUSTMENT_REFUND

NOW = 1_794_300_000_000


def _items() -> list[dict]:
    return [
        {
            "id": "ordli_ond",
            "title": "Caja de Velas de deseos onduladas",
            "quantity": 10,
            "unit_price": 22500,
            "metadata": {"aroma": "Navidad", "handle": "velas-de-deseos-onduladas"},
        },
        {
            "id": "ordli_cua",
            "title": "Caja de Velas de deseos cuadradas",
            "quantity": 10,
            "unit_price": 25000,
            "metadata": {"aroma": "Navidad", "handle": "velas-de-deseos-cuadradas"},
        },
    ]


class TestPlanLinePrices:
    def test_changed_line_gets_new_price_and_keeps_list_price(self) -> None:
        updates = plan_line_prices(
            _items(), {"ordli_ond": 18000}, by="edgm", reason="Precio por mayor", now_ms=NOW
        )

        assert len(updates) == 1
        up = updates[0]
        assert up.item_id == "ordli_ond"
        assert up.quantity == 10
        assert up.unit_price_cop == 18000
        assert up.metadata["list_unit_price_cop"] == 22500
        assert up.metadata["price_adjusted_reason"] == "Precio por mayor"
        assert up.metadata["price_adjusted_by"] == "edgm"
        assert up.metadata["price_adjusted_at_ms"] == NOW

    def test_same_price_is_not_an_update(self) -> None:
        updates = plan_line_prices(
            _items(), {"ordli_ond": 22500, "ordli_cua": 20000}, by="h", reason="x", now_ms=NOW
        )
        assert [u.item_id for u in updates] == ["ordli_cua"]

    def test_second_adjustment_keeps_the_original_list_price(self) -> None:
        items = _items()
        items[0]["unit_price"] = 18000
        items[0]["metadata"]["list_unit_price_cop"] = 22500

        (up,) = plan_line_prices(items, {"ordli_ond": 17000}, by="h", reason="x", now_ms=NOW)

        assert up.metadata["list_unit_price_cop"] == 22500

    def test_unknown_line_is_rejected(self) -> None:
        with pytest.raises(PriceAdjustmentError, match="no es una línea"):
            plan_line_prices(_items(), {"ordli_otra": 1000}, by="h", reason="x", now_ms=NOW)

    @pytest.mark.parametrize("bad", [0, -5, 1.5, "18000", True, None])
    def test_price_must_be_a_positive_integer(self, bad: object) -> None:
        with pytest.raises(PriceAdjustmentError, match="entero mayor que 0"):
            plan_line_prices(_items(), {"ordli_ond": bad}, by="h", reason="x", now_ms=NOW)  # type: ignore[dict-item]

    @pytest.mark.parametrize("reason", ["", "   "])
    def test_reason_is_required(self, reason: str) -> None:
        with pytest.raises(PriceAdjustmentError, match="motivo"):
            plan_line_prices(_items(), {"ordli_ond": 18000}, by="h", reason=reason, now_ms=NOW)


class TestPriceAdjustmentPatch:
    def test_history_records_who_why_and_the_totals(self) -> None:
        md = {
            "hubara_stage": "delivered",
            "hubara_stage_history": [{"from": "shipping", "to": "delivered", "at_ms": 1, "by": "human"}],
        }

        patch = build_price_adjustment_patch(
            md,
            by="edgm",
            reason="Precio por mayor negociado",
            total_before_cop=475000,
            total_after_cop=380000,
            refunded_cop=0,
            now_ms=NOW,
        )

        last = patch["hubara_stage_history"][-1]
        assert last == {
            "from": "delivered",
            "to": "delivered",
            "at_ms": NOW,
            "by": "edgm",
            "event": "price_adjusted",
            "note": "Precio por mayor negociado",
            "total_before_cop": 475000,
            "total_after_cop": 380000,
        }
        assert len(patch["hubara_stage_history"]) == 2
        assert META_KEY_PRICE_ADJUSTMENT_REFUND not in patch

    def test_refund_of_the_difference_accumulates(self) -> None:
        md = {"hubara_stage": "delivered", META_KEY_PRICE_ADJUSTMENT_REFUND: 10000}

        patch = build_price_adjustment_patch(
            md,
            by="edgm",
            reason="x",
            total_before_cop=475000,
            total_after_cop=380000,
            refunded_cop=95000,
            now_ms=NOW,
        )

        assert patch[META_KEY_PRICE_ADJUSTMENT_REFUND] == 105000
        assert patch["hubara_stage_history"][-1]["refunded_cop"] == 95000


class TestPayStatusAfterAdjustment:
    """Ajustar un pedido ya cobrado reembolsa en Medusa la diferencia (Medusa
    queda en `partially_refunded`). No es una devolución: el cliente pagó el
    precio negociado, así que el pedido sigue «Pagado»."""

    def test_paid_order_with_price_adjustment_refund_stays_paid(self) -> None:
        from src.platform.orders.medusa_order_query import resolve_pay_status

        md = {
            "hubara_payment_confirmed": True,
            "hubara_payment_confirmed_at_ms": 10,
            META_KEY_PRICE_ADJUSTMENT_REFUND: 95000,
        }
        assert resolve_pay_status(md, "partially_refunded") == "paid"

    def test_full_refund_is_still_a_refund(self) -> None:
        """Un reembolso total (pedido #32) no se esconde detrás del ajuste."""
        from src.platform.orders.medusa_order_query import resolve_pay_status

        md = {
            "hubara_payment_confirmed": True,
            "hubara_payment_confirmed_at_ms": 10,
            META_KEY_PRICE_ADJUSTMENT_REFUND: 95000,
        }
        assert resolve_pay_status(md, "refunded") == "refund"


class TestTimeline:
    def test_price_adjustment_shows_in_the_inspector_timeline_with_totals(self) -> None:
        from src.platform.orders.medusa_order_query import _build_timeline

        timeline = _build_timeline(
            stage_history=[{
                "from": "delivered", "to": "delivered", "at_ms": NOW, "by": "edgm",
                "event": "price_adjusted", "note": "Precio por mayor negociado",
                "total_before_cop": 475000, "total_after_cop": 380000, "refunded_cop": 95000,
            }],
            created_at_ms=1,
            is_draft=False,
            transactions=[],
            currency_code="cop",
        )

        (event,) = timeline
        assert event.label == "Precio ajustado"
        assert event.detail == (
            "por edgm · Precio por mayor negociado · $475.000 → $380.000"
            " · se reembolsaron $95.000 en Medusa (corrige el cobro)"
        )
