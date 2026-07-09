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


def test_messagingkit_reexports_rate_card_accessor():
    import src.platform.whatsapp.composition as impl
    import src.sdk.messagingkit as kit

    assert kit.get_current_rate_card is impl.get_current_rate_card
