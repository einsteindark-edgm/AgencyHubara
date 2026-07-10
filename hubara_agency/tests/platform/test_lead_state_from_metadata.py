"""Tests de `lead_state_from_metadata` — la derivación ÚNICA metadata→LeadState.

Se escribe UNA vez platform-side y la comparten:
  * el gate `check_reengagement_policy_activity` del remarketing (WS-B2), y
  * la activity que arma el snapshot para el agente Window Strategist (WS-B4).

Así el agente GraphAgents recibe el LeadState YA digerido y el espejo se
reduce a la matriz de precedencia (ver GRAPHAGENTS_WINDOW_STRATEGIST_PLAN.md,
Decisión #2).
"""
from __future__ import annotations

from src.platform.whatsapp.send_policy import lead_state_from_metadata


class TestTagAndCtwa:
    def test_tag_and_ctwa_clids_seen_map_to_flags(self):
        lead = lead_state_from_metadata(
            {"tag": "INTERESADO", "ctwa_clids_seen": ["clid_abc"]}
        )
        assert lead.tag == "INTERESADO"
        assert lead.is_ctwa_lead is True


class TestTransactionalHooks:
    def test_order_draft_with_slots_in_last_episode_sets_hook(self):
        meta = {
            "episodes": [
                {
                    "episode_id": "ep_001",
                    "closed_at_ms": None,
                    "order_draft": {"slots": {"producto": "camisa"}},
                }
            ]
        }
        lead = lead_state_from_metadata(meta)
        assert lead.has_order_draft is True

    def test_order_draft_with_empty_slots_is_not_a_hook(self):
        meta = {
            "episodes": [
                {
                    "episode_id": "ep_001",
                    "closed_at_ms": None,
                    "order_draft": {"slots": {}},
                }
            ]
        }
        lead = lead_state_from_metadata(meta)
        assert lead.has_order_draft is False

    def test_order_id_in_last_episode_sets_registered_order(self):
        # El gancho persiste aunque el episodio haya cerrado (caso
        # CONFIRMADO_PAGO_PENDIENTE: episodio cerrado + orden colocada).
        meta = {
            "tag": "CONFIRMADO_PAGO_PENDIENTE",
            "episodes": [
                {
                    "episode_id": "ep_001",
                    "closed_at_ms": 1_716_000_000_000,
                    "order_id": "order_123",
                }
            ],
        }
        lead = lead_state_from_metadata(meta)
        assert lead.has_registered_order is True
        assert lead.transactional_hook is True


class TestEngagement:
    def test_inbound_after_last_outbound_means_engaged(self):
        meta = {
            "last_inbound_at_ms": 2_000,
            "last_outbound": {"sent_at_ms": 1_000, "wa_message_id": "wamid.x"},
        }
        assert lead_state_from_metadata(meta).engaged is True

    def test_no_reply_since_last_outbound_means_not_engaged(self):
        meta = {
            "last_inbound_at_ms": 1_000,
            "last_outbound": {"sent_at_ms": 2_000, "wa_message_id": "wamid.x"},
        }
        assert lead_state_from_metadata(meta).engaged is False

    def test_inbound_without_any_outbound_means_engaged(self):
        # El lead escribió y el bot todavía no respondió — está activo.
        assert lead_state_from_metadata({"last_inbound_at_ms": 1_000}).engaged is True


class TestPaidMarketingOptIn:
    def test_default_is_no_paid_marketing(self):
        assert lead_state_from_metadata({}).allow_paid_marketing is False

    def test_explicit_operator_optin_enables_paid_marketing(self):
        meta = {"allow_paid_marketing": True}
        assert lead_state_from_metadata(meta).allow_paid_marketing is True
