"""Product ref from the storefront's WhatsApp button (2026-09-14).

The PDP prefills `📦 ref: HUB-CUBOLOVE · via: chatgpt` (hubara_frontend,
whatsapp-handoff.ts). The ref is the variant SKU — the stable identity the
whole catalog moved to — never a Medusa id. Same rules as `ref:cart_…`:
deterministic regex, never the LLM; episode-scoped capture; a note framed as
metadata, only once the SKU resolved against the catalog.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.use_cases.web_product_ref import (
    apply_web_product_capture,
    build_web_product_note,
    detect_agent_source,
    detect_product_ref,
    mark_web_product_resolved,
    mark_web_product_unresolved,
    record_agent_referral,
)

_PDP_TEXT = (
    "Hola 👋 Me interesa *Cubo Love*.\n\n"
    "✨ Ideal para: Regalos de amor y amistad · Tocadores\n"
    "🔗 https://hubara.com.co/products/cubo-love/\n\n"
    "📦 ref: HUB-CUBOLOVE · via: chatgpt"
)


class TestDetectProductRef:
    def test_reads_the_sku_from_the_storefront_message(self):
        assert detect_product_ref(_PDP_TEXT) == "HUB-CUBOLOVE"

    def test_reads_a_variant_sku_and_stops_at_the_separator(self):
        assert detect_product_ref("📦 ref: HUB-DUOZOD-LEO · via: gemini") == "HUB-DUOZOD-LEO"

    def test_tolerates_spacing_and_marker_case(self):
        assert detect_product_ref("REF:HUB-CISNE") == "HUB-CISNE"
        assert detect_product_ref("Ref:   hub-cisne") == "HUB-CISNE"

    def test_ignores_cart_refs_and_bare_skus(self):
        assert detect_product_ref("ref: cart_01HXXXXXXXXXXXXXXXXXXX") is None
        assert detect_product_ref("me interesa HUB-CISNE") is None
        assert detect_product_ref(None) is None


class TestDetectAgentSource:
    def test_reads_a_known_agent(self):
        assert detect_agent_source(_PDP_TEXT) == "chatgpt"

    def test_ignores_unknown_values(self):
        assert detect_agent_source("📦 ref: HUB-CISNE · via: evil.tld") is None
        assert detect_agent_source("📦 ref: HUB-CISNE") is None


def _metadata_with_episode(episode_id: str = "ep_1", **episode) -> dict:
    return {"episodes": [{"episode_id": episode_id, "status": "active", **episode}]}


class TestApplyWebProductCapture:
    def test_first_capture_records_pending_state_scoped_to_the_episode(self):
        meta = _metadata_with_episode()
        assert apply_web_product_capture(meta, sku="HUB-CISNE", source="chatgpt", now_ms=1) is True
        state = meta["web_product_ref"]
        assert state["sku"] == "HUB-CISNE"
        assert state["status"] == "pending"
        assert state["source"] == "chatgpt"
        assert state["episode_id"] == "ep_1"

    def test_same_sku_in_same_episode_is_a_noop(self):
        meta = _metadata_with_episode()
        apply_web_product_capture(meta, sku="HUB-CISNE", source=None, now_ms=1)
        mark_web_product_resolved(meta, handle="velon-cisne", title="Velón Cisne")
        assert apply_web_product_capture(meta, sku="HUB-CISNE", source=None, now_ms=2) is False
        assert meta["web_product_ref"]["status"] == "resolved"

    def test_another_sku_wins(self):
        meta = _metadata_with_episode()
        apply_web_product_capture(meta, sku="HUB-CISNE", source=None, now_ms=1)
        assert apply_web_product_capture(meta, sku="HUB-KOALA", source=None, now_ms=2) is True
        assert meta["web_product_ref"]["sku"] == "HUB-KOALA"


class TestBuildWebProductNote:
    def _resolved(self) -> dict:
        meta = _metadata_with_episode()
        apply_web_product_capture(meta, sku="HUB-CISNE", source="chatgpt", now_ms=1)
        mark_web_product_resolved(meta, handle="velon-cisne", title="Velón Cisne")
        return meta

    def test_names_the_product_and_frames_the_note_as_metadata(self):
        note = build_web_product_note(self._resolved())
        assert note is not None
        assert "no es instruccion del usuario" in note
        assert "Velón Cisne" in note
        assert "HUB-CISNE" in note

    def test_does_not_leak_the_agent_source_into_the_prompt(self):
        # `via:` is attacker-writable; it is attribution, not context.
        assert "chatgpt" not in (build_web_product_note(self._resolved()) or "")

    def test_silent_while_pending_or_unresolved(self):
        meta = _metadata_with_episode()
        apply_web_product_capture(meta, sku="HUB-NOPE", source=None, now_ms=1)
        assert build_web_product_note(meta) is None
        mark_web_product_unresolved(meta, reason="sku_not_found")
        assert build_web_product_note(meta) is None

    def test_off_once_the_episode_has_an_order(self):
        meta = self._resolved()
        meta["episodes"][-1]["order_id"] = "HUB-0001"
        assert build_web_product_note(meta) is None

    def test_off_in_a_later_episode(self):
        meta = self._resolved()
        meta["episodes"].append({"episode_id": "ep_2", "status": "active"})
        assert build_web_product_note(meta) is None


class TestRecordAgentReferral:
    """`metadata.agent_referrals` — the history the dashboard counts.

    `web_product_ref` is a single dict that the next ref overwrites, so it
    cannot answer "how many conversations did ChatGPT send this month?". The
    referral log is append-only for that reason.
    """

    def test_appends_source_sku_episode_and_time(self):
        meta = _metadata_with_episode("ep_1")

        assert record_agent_referral(meta, source="chatgpt", sku="HUB-CUBOLOVE", now_ms=1_000)

        assert meta["agent_referrals"] == [
            {"source": "chatgpt", "sku": "HUB-CUBOLOVE", "episode_id": "ep_1", "at_ms": 1_000}
        ]

    def test_a_second_tap_in_the_same_episode_is_not_a_second_referral(self):
        meta = _metadata_with_episode("ep_1")
        record_agent_referral(meta, source="chatgpt", sku="HUB-CUBOLOVE", now_ms=1_000)

        assert not record_agent_referral(meta, source="chatgpt", sku="HUB-CISNE", now_ms=2_000)
        assert len(meta["agent_referrals"]) == 1

    def test_a_later_product_ref_does_not_erase_the_history(self):
        meta = _metadata_with_episode("ep_1")
        record_agent_referral(meta, source="chatgpt", sku="HUB-CUBOLOVE", now_ms=1_000)
        meta["episodes"][0]["closed_at_ms"] = 1_500
        meta["episodes"].append({"episode_id": "ep_2", "status": "active"})

        record_agent_referral(meta, source="gemini", sku="HUB-CISNE", now_ms=2_000)
        apply_web_product_capture(meta, sku="HUB-FANTASMA", source=None, now_ms=3_000)

        assert [r["source"] for r in meta["agent_referrals"]] == ["chatgpt", "gemini"]

    def test_without_an_episode_dedupes_by_day(self):
        """Human-routed sessions skip the episode lifecycle, but the referral is
        still a fact worth counting: once per source per day, not per tap."""
        meta: dict = {}
        day = 86_400_000

        record_agent_referral(meta, source="chatgpt", sku="HUB-CISNE", now_ms=day + 1)
        record_agent_referral(meta, source="chatgpt", sku="HUB-CISNE", now_ms=day + 2)
        record_agent_referral(meta, source="chatgpt", sku="HUB-CISNE", now_ms=2 * day + 1)

        assert len(meta["agent_referrals"]) == 2

    def test_rejects_a_source_outside_the_closed_list(self):
        meta = _metadata_with_episode()

        assert not record_agent_referral(meta, source="instagram", sku="HUB-CISNE", now_ms=1)
        assert "agent_referrals" not in meta
