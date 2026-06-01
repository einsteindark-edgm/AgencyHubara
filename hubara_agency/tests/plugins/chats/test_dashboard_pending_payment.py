"""Tests del helper `_compute_pending_payment_order_id` (dashboard chats).

Decide cuándo una sesión tiene un pedido esperando que un humano confirme el
pago — la señal que enciende el botón "Confirmar pago" en el chat. La señal
viaja por el mismo payload de sesión que el SSE del dashboard ya emite.

Caso real que motivó esto (vault `wa_573125671604`): el agente registró el
pedido en Medusa y escaló con `PAYMENT_VERIFICATION_PENDING`; el handoff dejó
`tag=HUMANO`, `active_route=humano`, `escalation_reason=PAYMENT_VERIFICATION_PENDING`
y `registered_order.success=True`.
"""
from __future__ import annotations

from src.plugins.chats.api.dashboard import _compute_pending_payment_order_id

ORDER_ID = "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z"


def _pending_session(**overrides) -> dict:
    """Sesión en el estado canónico 'esperando confirmación de pago'."""
    data = {
        "active_route": "humano",
        "escalation_reason": "PAYMENT_VERIFICATION_PENDING",
        "tag": "HUMANO",
        "registered_order": {"order_id": ORDER_ID, "success": True},
        "episodes": [{"order_id": ORDER_ID}],
    }
    data.update(overrides)
    return data


def test_pending_session_returns_order_id():
    assert _compute_pending_payment_order_id(_pending_session()) == ORDER_ID


def test_not_human_route_returns_none():
    # Mientras el bot gestiona, no hay nada que confirme un humano.
    assert _compute_pending_payment_order_id(
        _pending_session(active_route="ventas")
    ) is None


def test_other_escalation_reason_returns_none():
    # Escaló por otra cosa (ej. descuento) — no es confirmación de pago.
    assert _compute_pending_payment_order_id(
        _pending_session(escalation_reason="DISCOUNT_REQUEST")
    ) is None


def test_no_escalation_reason_returns_none():
    data = _pending_session()
    del data["escalation_reason"]
    assert _compute_pending_payment_order_id(data) is None


def test_tag_compra_exitosa_returns_none():
    # El humano ya confirmó el pago → tag terminal → el botón desaparece.
    assert _compute_pending_payment_order_id(
        _pending_session(tag="COMPRA_EXITOSA")
    ) is None


def test_tag_rechazo_returns_none():
    # Pedido cancelado → terminal.
    assert _compute_pending_payment_order_id(
        _pending_session(tag="RECHAZO")
    ) is None


def test_episode_payment_confirmed_returns_none():
    # `apply_payment_confirmation_to_chat_metadata` marca el episodio; aunque el
    # tag no se haya actualizado, el pago ya está confirmado.
    assert _compute_pending_payment_order_id(
        _pending_session(
            episodes=[
                {"order_id": ORDER_ID, "payment_confirmed_at_ms": 1780090366836}
            ],
        )
    ) is None


def test_no_registered_order_returns_none():
    data = _pending_session()
    del data["registered_order"]
    assert _compute_pending_payment_order_id(data) is None


def test_registered_order_failed_returns_none():
    # El registro en Medusa falló → no hay pedido que confirmar.
    assert _compute_pending_payment_order_id(
        _pending_session(registered_order={"order_id": ORDER_ID, "success": False})
    ) is None


def test_registered_order_missing_id_returns_none():
    assert _compute_pending_payment_order_id(
        _pending_session(registered_order={"success": True})
    ) is None


def test_empty_metadata_returns_none():
    assert _compute_pending_payment_order_id({}) is None


def test_pending_without_episodes_still_returns_order_id():
    # Falta el array de episodes (metadata vieja) — igual está pendiente.
    data = _pending_session()
    del data["episodes"]
    assert _compute_pending_payment_order_id(data) == ORDER_ID
