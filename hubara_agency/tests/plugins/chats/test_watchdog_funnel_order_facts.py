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


class TestInternalLabel:
    """«Resumen interno en una plantilla» (motor de decisiones, familia C):
    el `motivo` es prosa que el LLM escribió al etiquetar y hoy entra tal
    cual como variable de la plantilla. Si el motor decide que no es un
    texto para el cliente, va el genérico."""

    def _spec(self) -> TemplateSpec:
        return TemplateSpec(
            name="watchdog_q", category="marketing", language="es_CO", waba_template_name="watchdog_q",
            semantics="retomar la cotización", triggers_when_window_expiring=True,
            requires_episode_stage="awaiting_quote",
            variables=(TemplateVariable(name="product_or_quote_label", type="string", max_length=None,
                                        description="producto"),),
        )

    def test_today_the_motivo_goes_as_it_is(self) -> None:
        md = {**_metadata(), "motivo": "Cliente pidió precio del Cubo Love; se le cotizó y no respondió"}
        assert _resolve_template_variables(self._spec(), md)["product_or_quote_label"] == md["motivo"]

    def test_an_internal_motivo_is_replaced_by_the_generic_label(self) -> None:
        md = {**_metadata(), "motivo": "Cliente pidió precio del Cubo Love; se le cotizó y no respondió"}
        assert _resolve_template_variables(self._spec(), md, motivo_ok=False)["product_or_quote_label"] == "tu consulta"


async def test_with_jev_the_watchdog_does_not_send_an_internal_summary(_isolate_vault_dir, monkeypatch) -> None:
    from src.plugins.chats.agent.sales.decisions.remarketing_context import register_label_decision
    from src.plugins.chats.shared import agent_decisions

    monkeypatch.setattr(agent_decisions, "_label_decider", None)  # se restaura al terminar
    register_label_decision()

    import src.platform.perception.adapters.fake as fake_mod
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    monkeypatch.setenv("DECISIONS_BOT", "B")
    answer = TypedAnswer(id="egreso.destinatario", kind="choice", choice="reporte_interno",
                         probs=(("reporte_interno", 0.93),), confidence=0.93)
    fake = fake_mod.FakePerceptionAdapter({"egreso.destinatario": answer})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)

    internal = await agent_decisions.label_is_internal(
        session_id="wa_573001234567", text="Cliente pidió precio del Cubo Love; se le cotizó y no respondió",
        vault_dir=_isolate_vault_dir,
    )
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    today = await agent_decisions.label_is_internal(
        session_id="wa_573001234567", text="Cliente pidió precio del Cubo Love", vault_dir=_isolate_vault_dir,
    )

    assert internal is True and today is False
