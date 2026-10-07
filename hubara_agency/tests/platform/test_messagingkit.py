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


def test_messagingkit_reexports_standby_ear_helpers():
    # D1.4: el oído `standby` (chats) anota los ecos de MBA como outbound
    # pendiente de pricing y reabre la ventana de servicio con cada inbound,
    # vía SDK — P-28 le prohíbe `src.platform.whatsapp.{activities,cost,window}`.
    import src.platform.whatsapp.activities as activities
    import src.platform.whatsapp.cost as cost
    import src.platform.whatsapp.window as window
    import src.sdk.messagingkit as kit

    assert kit.OutboundLogEntry is cost.OutboundLogEntry
    assert kit.record_outbound_in_active_episode is activities.record_outbound_in_active_episode
    assert kit.compute_service_window_expiry is window.compute_service_window_expiry


def test_messagingkit_reexports_the_sales_turn_sends():
    # Motor de decisiones F4: el workflow de ventas V2 manda el texto, el
    # "escribiendo…" y los eventos de Meta (CAPI) vía SDK — P-28 le prohíbe
    # `src.platform.whatsapp.{activities,capi_activity}` directo.
    import src.platform.whatsapp.activities as activities
    import src.platform.whatsapp.capi_activity as capi
    import src.sdk.messagingkit as kit

    assert getattr(kit, "send_whatsapp_message_activity", None) is activities.send_whatsapp_message_activity
    assert getattr(kit, "send_typing_indicator_activity", None) is activities.send_typing_indicator_activity
    assert getattr(kit, "send_capi_event_activity", None) is capi.send_capi_event_activity
    assert getattr(kit, "flush_capi_outbox_activity", None) is capi.flush_capi_outbox_activity


def test_messagingkit_reexports_reengagement_frequency():
    # Dashboard Agents → Remarketing → Frecuencia: el plugin `reengagement`
    # (snapshot + etiqueta SIN_RESPUESTA) y `chats` (API de ajustes) leen y
    # escriben el tope de toques por SDK — P-28 les prohíbe platform directo.
    import src.platform.whatsapp.reengagement_frequency as freq
    import src.platform.whatsapp.reengagement_ladder as ladder
    import src.sdk.messagingkit as kit

    assert kit.clamp_max_steps is ladder.clamp_max_steps
    assert kit.effective_max_touches is freq.effective_max_touches
    assert kit.set_max_touches is freq.set_max_touches
    assert kit.read_frequency_state is freq.read_state
    assert kit.frequency_ceiling is freq.ceiling
    assert kit.FrequencyState is freq.FrequencyState
