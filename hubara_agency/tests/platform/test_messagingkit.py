"""Regla de oro del SDK: `src.sdk.messagingkit` re-exporta la central de
decisión de envío (send_policy) para plugins — mismo patrón que dashboardkit.

Consumidor: el plugin `reengagement` (Window Strategist) arma el snapshot con
`lead_state_from_metadata` sin importar `src.platform` (P-28).
"""
from __future__ import annotations


def test_messagingkit_reexports_send_policy_symbols():
    import src.platform.whatsapp.send_policy as impl
    import src.sdk.messagingkit as kit

    assert kit.LeadState is impl.LeadState
    assert kit.SendDecision is impl.SendDecision
    assert kit.lead_state_from_metadata is impl.lead_state_from_metadata
    assert kit.decide_reengagement is impl.decide_reengagement
    assert kit.evaluate_send is impl.evaluate_send


def test_messagingkit_reexports_quiet_hours():
    import src.platform.whatsapp.quiet_hours as impl
    import src.sdk.messagingkit as kit

    assert kit.is_quiet_hours_for_session is impl.is_quiet_hours_for_session
    assert kit.resolve_local_timezone is impl.resolve_local_timezone


def test_messagingkit_reexports_reengagement_index():
    # Punto 2 (escala): el ingest (chats) y el snapshot builder (reengagement)
    # usan el índice vía SDK — P-28 les prohíbe platform directo.
    import src.platform.whatsapp.reengagement_index as impl
    import src.sdk.messagingkit as kit

    assert kit.load_reengagement_index is impl.load_index
    assert kit.update_reengagement_index_entry is impl.update_index_entry
    assert kit.update_reengagement_index_entries is impl.update_index_entries
    assert kit.reengagement_shortlist is impl.shortlist_session_ids


def test_messagingkit_reexports_rate_card_accessor():
    import src.platform.whatsapp.composition as impl
    import src.sdk.messagingkit as kit

    assert kit.get_current_rate_card is impl.get_current_rate_card


def test_messagingkit_reexports_template_send():
    # El plugin `marketing` (campañas directas) manda templates aprobados vía
    # SDK — P-28 le prohíbe `src.platform.whatsapp.activities` directo. Expone
    # la activity (para registrar en su worker) y la función pura (tests).
    import src.platform.whatsapp.activities as impl
    import src.sdk.messagingkit as kit

    assert kit.send_whatsapp_template_activity is impl.send_whatsapp_template_activity
    assert kit.send_template_to_session is impl.send_template_to_session


def test_messagingkit_reexports_service_window_guard():
    # El endpoint del operador (chats) chequea la ventana 24h vía SDK — P-28 le
    # prohíbe `src.platform.whatsapp.window` directo.
    import src.platform.whatsapp.window as impl
    import src.sdk.messagingkit as kit

    assert kit.is_service_window_closed is impl.is_service_window_closed
