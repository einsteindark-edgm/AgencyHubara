"""El scoring del cliente decide "compró" por el PEDIDO, no por la etiqueta.

Continuación de la migración a OrderFacts (pedido #31/#32): el LTV, la
frecuencia y la última compra salían de `closing_tag == COMPRA_EXITOSA` + un
total que el endpoint fetcheaba por su cuenta. Ahora:

  * pedido pagado y no cancelado → cuenta como compra, con su total vivo;
  * pendiente de pago → parcial (no infla el LTV);
  * cancelado o reembolsado → no cuenta como compra;
  * Medusa no responde → se conserva el comportamiento por etiqueta (legacy).
"""
from __future__ import annotations

from typing import Any

import pytest

from src.platform.customer_scoring.features import compute_customer_features
from src.platform.orders.facts import OrderFacts, OrderFactsSnapshot

NOW = 1_758_000_000_000
DAY = 24 * 60 * 60 * 1000


def _fact(
    order_id: str,
    total: int,
    *,
    pay: str = "paid",
    stage: str = "delivered",
    created_at_ms: int = NOW - 2 * DAY,
) -> OrderFacts:
    return OrderFacts(
        order_id=order_id,
        display_id="#31",
        total_cop=total,
        currency_code="cop",
        pay_status=pay,
        stage=stage,
        customer="Ana",
        is_draft=False,
        created_at_ms=created_at_ms,
    )


def _ep(order_id: str | None, *, closing_tag: str | None = "COMPRA_EXITOSA") -> dict[str, Any]:
    return {
        "episode_id": f"ep_{order_id}",
        "started_at_ms": NOW - 10 * DAY,
        "closed_at_ms": NOW - 9 * DAY,
        "closing_tag": closing_tag,
        "order_id": order_id,
    }


def _features(episodes: list[dict], snapshot: OrderFactsSnapshot):
    return compute_customer_features(
        {"episodes": episodes}, now_ms=NOW, order_facts=snapshot
    )


def test_paid_order_counts_with_live_total_and_medusa_date() -> None:
    snap = OrderFactsSnapshot(facts={"order_31": _fact("order_31", 120000)})
    f = _features([_ep("order_31")], snap)
    assert f.episodes_won == 1
    assert f.monetary_cop == 120000
    assert f.last_purchase_order_id == "order_31"
    assert f.recency_days == 2  # created_at de Medusa, no closed_at del chat


def test_unpaid_order_is_partial_not_won() -> None:
    snap = OrderFactsSnapshot(facts={"order_31": _fact("order_31", 120000, pay="pending")})
    f = _features([_ep("order_31")], snap)
    assert (f.episodes_won, f.episodes_partial, f.monetary_cop) == (0, 1, 0)
    assert f.last_purchase_at_ms is None


@pytest.mark.parametrize(
    "fact",
    [
        _fact("order_31", 120000, pay="refund"),
        _fact("order_31", 120000, stage="cancelled"),
    ],
)
def test_refunded_or_cancelled_order_counts_as_lost(fact: OrderFacts) -> None:
    f = _features([_ep("order_31")], OrderFactsSnapshot(facts={"order_31": fact}))
    assert (f.episodes_won, f.episodes_lost, f.monetary_cop) == (0, 1, 0)


def test_order_missing_in_medusa_does_not_count() -> None:
    f = _features([_ep("order_31")], OrderFactsSnapshot())
    assert (f.episodes_won, f.monetary_cop) == (0, 0)


def test_medusa_down_keeps_legacy_tag_behaviour() -> None:
    """Sin datos del pedido no inventamos: vale la etiqueta del chat."""
    snap = OrderFactsSnapshot(unresolved=frozenset({"order_31"}), stale=True)
    f = compute_customer_features(
        {"episodes": [_ep("order_31")]},
        now_ms=NOW,
        medusa_order_totals_cop={"order_31": 90000},
        order_facts=snap,
    )
    assert (f.episodes_won, f.monetary_cop) == (1, 90000)


def test_two_orders_only_the_paid_one_adds_to_ltv() -> None:
    snap = OrderFactsSnapshot(
        facts={
            "order_31": _fact("order_31", 120000, created_at_ms=NOW - 5 * DAY),
            "order_40": _fact("order_40", 50000, pay="pending"),
        }
    )
    f = _features([_ep("order_31"), _ep("order_40")], snap)
    assert f.monetary_cop == 120000
    assert f.frequency_total == 1
    assert f.last_purchase_order_id == "order_31"


def test_without_order_facts_the_legacy_path_is_untouched() -> None:
    """Los callers viejos (sin snapshot) siguen igual — contrato preservado."""
    f = compute_customer_features(
        {"episodes": [_ep("order_31")]},
        now_ms=NOW,
        medusa_order_totals_cop={"order_31": 90000},
    )
    assert (f.episodes_won, f.monetary_cop) == (1, 90000)
