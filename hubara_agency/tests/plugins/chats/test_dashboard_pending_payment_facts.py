"""El botón "Confirmar pago" del chat lo decide el PEDIDO, no la etiqueta.

Antes se miraba `tag == COMPRA_EXITOSA` / `episode.payment_confirmed_at_ms`:
copias en el chat que se desincronizan cuando el operador toca Medusa Admin.
Casos reales que esto arregla:

  * el operador confirma el pago en Medusa Admin (sin pasar por el dashboard)
    → el botón desaparece solo;
  * el operador reembolsa en Medusa (pedido #32) → el botón vuelve.

La RUTA de la conversación no se toca: el botón sigue siendo solo para chats
en la bandeja humana escalados por verificación de pago.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.platform.orders.facts import OrderFacts, OrderFactsSnapshot
from src.plugins.chats.api.dashboard import _compute_pending_payment_order_id


def _fact(pay: str = "paid", stage: str = "preparing") -> OrderFacts:
    return OrderFacts(
        order_id="order_32", display_id="#32", total_cop=50000, currency_code="cop",
        pay_status=pay, stage=stage, customer="Ana", is_draft=False, created_at_ms=1,
    )


def _chat(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "tag": "HUMANO",
        "active_route": "humano",
        "escalation_reason": "PAYMENT_VERIFICATION_PENDING",
        "registered_order": {"success": True, "order_id": "order_32"},
        "episodes": [{"episode_id": "ep_1", "order_id": "order_32"}],
    }
    data.update(over)
    return data


def _snap(fact: OrderFacts | None = None, *, unresolved: bool = False) -> OrderFactsSnapshot:
    if unresolved:
        return OrderFactsSnapshot(unresolved=frozenset({"order_32"}), stale=True)
    return OrderFactsSnapshot(facts={"order_32": fact} if fact else {})


def test_unpaid_order_shows_the_button() -> None:
    assert _compute_pending_payment_order_id(_chat(), _snap(_fact(pay="pending"))) == "order_32"


def test_paid_in_medusa_hides_the_button_even_if_the_chat_tag_lags() -> None:
    """Pago registrado en Medusa Admin: el chat sigue diciendo HUMANO."""
    assert _compute_pending_payment_order_id(_chat(), _snap(_fact())) is None


def test_refunded_in_medusa_shows_the_button_again(_unused: None = None) -> None:
    """Pedido #32: confirmado por error y reembolsado en Medusa."""
    chat = _chat(tag="COMPRA_EXITOSA")
    chat["episodes"][0]["payment_confirmed_at_ms"] = 1_700_000_000_000
    assert _compute_pending_payment_order_id(chat, _snap(_fact(pay="refund"))) == "order_32"


def test_cancelled_order_never_shows_the_button() -> None:
    assert _compute_pending_payment_order_id(_chat(), _snap(_fact(pay="pending", stage="cancelled"))) is None


def test_order_gone_from_medusa_does_not_show_the_button() -> None:
    assert _compute_pending_payment_order_id(_chat(), _snap()) is None


@pytest.mark.parametrize("over", [{"active_route": "ventas"}, {"escalation_reason": "OTRA"}])
def test_conversation_state_still_gates_the_button(over: dict[str, Any]) -> None:
    """Sin bandeja humana + escalación por pago, no hay botón (no cambiamos ruteo)."""
    assert _compute_pending_payment_order_id(_chat(**over), _snap(_fact(pay="pending"))) is None


def test_medusa_down_keeps_the_legacy_tag_rule() -> None:
    assert _compute_pending_payment_order_id(_chat(), _snap(unresolved=True)) == "order_32"
    chat = _chat(tag="COMPRA_EXITOSA")
    assert _compute_pending_payment_order_id(chat, _snap(unresolved=True)) is None
