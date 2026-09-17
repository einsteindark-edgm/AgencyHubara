"""Watchdog y embudo: "pagado" lo dice el pedido, no la etiqueta del chat.

  * `_infer_episode_stage`: post-venta vs esperando pago según el pedido real
    (antes: `closing_tag == COMPRA_EXITOSA`), así el cliente no recibe un
    "te esperamos para pagar" cuando ya pagó, ni un "gracias por tu compra"
    cuando el pago se revirtió.
  * `_resolve_template_variables`: el monto del template sale del pedido
    (antes era el texto fijo "el monto del pedido").
  * `is_open_cart`: carrito abierto = pedido sin pagar.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.platform.orders.facts import OrderFacts, OrderFactsSnapshot
from src.platform.whatsapp.templates.registry import TemplateSpec, TemplateVariable
from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
    _infer_episode_stage,
    _resolve_template_variables,
)
from src.plugins.chats.shared.funnel import is_open_cart


def _fact(pay: str = "paid", *, stage: str = "preparing", total: int = 120000) -> OrderFacts:
    return OrderFacts(
        order_id="order_31", display_id="#31", total_cop=total, currency_code="cop",
        pay_status=pay, stage=stage, customer="Ana", is_draft=False, created_at_ms=1,
    )


def _snap(fact: OrderFacts | None = None, *, unresolved: bool = False) -> OrderFactsSnapshot:
    if unresolved:
        return OrderFactsSnapshot(unresolved=frozenset({"order_31"}), stale=True)
    return OrderFactsSnapshot(facts={"order_31": fact} if fact else {})


def _metadata(closing_tag: str | None = None) -> dict[str, Any]:
    return {
        "tag": "HUMANO",
        "registered_order": {"success": True, "order_id": "order_31", "total_cop": 90000},
        "episodes": [{"episode_id": "ep_1", "order_id": "order_31", "closing_tag": closing_tag}],
    }


class TestStage:
    def test_paid_order_is_post_purchase_even_without_the_tag(self) -> None:
        assert _infer_episode_stage(_metadata(), _snap(_fact())) == "post_purchase"

    def test_unpaid_order_is_awaiting_payment_even_with_the_tag(self) -> None:
        md = _metadata("COMPRA_EXITOSA")
        assert _infer_episode_stage(md, _snap(_fact(pay="refund"))) == "awaiting_payment"

    def test_without_order_facts_the_tag_decides(self) -> None:
        assert _infer_episode_stage(_metadata("COMPRA_EXITOSA")) == "post_purchase"
        assert _infer_episode_stage(_metadata()) == "awaiting_payment"
        assert _infer_episode_stage(_metadata("COMPRA_EXITOSA"), _snap(unresolved=True)) == "post_purchase"


class TestTemplateAmount:
    def _spec(self) -> TemplateSpec:
        return TemplateSpec(
            name="watchdog_x",
            category="marketing",
            language="es_CO",
            waba_template_name="watchdog_x",
            semantics="recordatorio de pago",
            triggers_when_window_expiring=True,
            requires_episode_stage="awaiting_payment",
            variables=(
                TemplateVariable(
                    name="amount_currency", type="string",
                    max_length=None, description="monto",
                ),
            ),
        )

    def test_amount_comes_from_the_order(self) -> None:
        vars_ = _resolve_template_variables(self._spec(), _metadata(), _snap(_fact(total=120000)))
        assert vars_["amount_currency"] == "$120.000 COP"

    def test_without_order_data_keeps_the_generic_text(self) -> None:
        vars_ = _resolve_template_variables(self._spec(), _metadata())
        assert vars_["amount_currency"] == "el monto del pedido"


class TestOpenCart:
    def test_unpaid_order_is_an_open_cart(self) -> None:
        md = _metadata()
        assert is_open_cart(md["episodes"][0], md, _snap(_fact(pay="pending"))) is True

    def test_paid_order_is_not_an_open_cart_even_without_the_tag(self) -> None:
        md = _metadata()
        assert is_open_cart(md["episodes"][0], md, _snap(_fact())) is False

    def test_refunded_order_is_an_open_cart_again(self) -> None:
        md = _metadata("COMPRA_EXITOSA")
        md["tag"] = "COMPRA_EXITOSA"
        assert is_open_cart(md["episodes"][0], md, _snap(_fact(pay="refund"))) is True

    def test_cancelled_order_is_not_a_cart(self) -> None:
        md = _metadata()
        assert is_open_cart(md["episodes"][0], md, _snap(_fact(pay="pending", stage="cancelled"))) is False

    @pytest.mark.parametrize("snap", [None, _snap(unresolved=True)])
    def test_without_order_facts_the_legacy_rule_holds(self, snap) -> None:
        md = _metadata()
        assert is_open_cart(md["episodes"][0], md, snap) is True
        md["tag"] = "COMPRA_EXITOSA"
        assert is_open_cart(md["episodes"][0], md, snap) is False
