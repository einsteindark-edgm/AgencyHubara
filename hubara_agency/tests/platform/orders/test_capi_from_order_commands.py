"""Eventos CAPI desde las acciones humanas sobre el pedido (auditoría 2026-09-08, punto 1).

El hallazgo crítico: el humano confirma el pago desde el dashboard y ese
camino nunca llamaba CAPI → Meta jamás recibía ``Purchase``. Ahora los
helpers puros que sincronizan el chat encolan el evento en el outbox del
chat, y el command port dispara un flush best-effort (durable por el
próximo cambio de etapa / turno si el proceso muere).
"""
from __future__ import annotations

from typing import Any

from src.platform.orders.medusa_order_command import (
    apply_order_cancellation_to_chat_metadata,
    apply_payment_confirmation_to_chat_metadata,
)
from src.plugins.orders.agent.activities.emit_stage import capi_event_for_stage

SESSION = "wa_573001234567"
NOW = 1_757_350_000_000


def _chat(**extra: Any) -> dict[str, Any]:
    md: dict[str, Any] = {
        "tag": "HUMANO",
        "active_route": "humano",
        "ctwa_referrals": [{"ctwa_clid": "CLID", "captured_at_ms": NOW - 1}],
        "registered_order": {"success": True, "order_id": "order_42", "total_cop": 120000, "currency": "COP"},
        "episodes": [
            {"episode_id": "ep_002", "closing_tag": "CONFIRMADO_PAGO_PENDIENTE", "order_id": "order_42", "order_total_cop": 120000},
        ],
    }
    md.update(extra)
    return md


class TestPaymentConfirmation:
    def test_enqueues_purchase_with_order_total(self) -> None:
        md = _chat()
        assert apply_payment_confirmation_to_chat_metadata(md, now_ms=NOW, by="edgm", session_id=SESSION, order_id="order_42")
        assert md["tag"] == "COMPRA_EXITOSA"
        assert [(e["event_name"], e["event_id"], e["value"], e["currency"], e["source"]) for e in md["capi_outbox"]] == [
            ("Purchase", "purchase_order_42", 120000, "COP", "confirm_payment")
        ]

    def test_value_falls_back_to_episode_total_when_registered_order_differs(self) -> None:
        md = _chat(registered_order={"success": True, "order_id": "order_99", "total_cop": 5, "currency": "COP"})
        apply_payment_confirmation_to_chat_metadata(md, now_ms=NOW, by=None, session_id=SESSION, order_id="order_42")
        assert md["capi_outbox"][0]["value"] == 120000

    def test_no_value_known_means_no_purchase_event(self) -> None:
        md = _chat(registered_order=None)
        md["episodes"][0].pop("order_total_cop")
        apply_payment_confirmation_to_chat_metadata(md, now_ms=NOW, by=None, session_id=SESSION, order_id="order_42")
        assert "capi_outbox" not in md

    def test_idempotent_second_confirmation_does_not_enqueue_twice(self) -> None:
        md = _chat()
        apply_payment_confirmation_to_chat_metadata(md, now_ms=NOW, by=None, session_id=SESSION, order_id="order_42")
        assert not apply_payment_confirmation_to_chat_metadata(md, now_ms=NOW + 1, by=None, session_id=SESSION, order_id="order_42")
        assert len(md["capi_outbox"]) == 1

    def test_organic_session_enqueues_nothing(self) -> None:
        md = _chat(ctwa_referrals=[])
        apply_payment_confirmation_to_chat_metadata(md, now_ms=NOW, by=None, session_id=SESSION, order_id="order_42")
        assert "capi_outbox" not in md

    def test_legacy_call_without_order_context_still_closes_the_chat(self) -> None:
        md = _chat()
        assert apply_payment_confirmation_to_chat_metadata(md, now_ms=NOW, by=None)
        assert md["tag"] == "COMPRA_EXITOSA"


class TestCancellation:
    def test_enqueues_order_canceled(self) -> None:
        md = _chat()
        assert apply_order_cancellation_to_chat_metadata(md, now_ms=NOW, reason="no pagó", by="edgm", session_id=SESSION, order_id="order_42")
        assert [(e["event_name"], e["event_id"], e["source"]) for e in md["capi_outbox"]] == [
            ("OrderCanceled", "ordercanceled_order_42", "cancel_order")
        ]

    def test_cancel_after_purchase_confirmed_keeps_state_and_enqueues_nothing(self) -> None:
        md = _chat(tag="COMPRA_EXITOSA")
        assert not apply_order_cancellation_to_chat_metadata(md, now_ms=NOW, session_id=SESSION, order_id="order_42")
        assert "capi_outbox" not in md


class TestStageMapping:
    def test_stages_map_to_post_purchase_events(self) -> None:
        assert capi_event_for_stage("shipping") == "OrderShipped"
        assert capi_event_for_stage("delivered") == "OrderDelivered"
        assert capi_event_for_stage("cancelled") == "OrderCanceled"
        assert capi_event_for_stage("preparing") is None
        assert capi_event_for_stage("ready") is None
