"""Verificación VIVA del nodo LLM narrativo (tests verdes ≠ feature viva). Corre SOLO si el
proxy LiteLLM del central está arriba (:4000); si no, se SKIPea — así el loop local y CI sin
proxy siguen verdes. Prueba el ciclo real: build(llm=LiteLLMProxy) → DeepSeek (deepseek-v4-flash)
→ una narrativa que **NO inventa números** (el guard sobre la salida REAL es lo que da confianza).
"""
from __future__ import annotations

import urllib.request

import pytest

pytest.importorskip("langgraph")


def _proxy_up() -> bool:
    try:
        urllib.request.urlopen("http://localhost:4000/health/liveliness", timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _proxy_up(), reason="no hay proxy LiteLLM en :4000")

REPORT = {
    "days": [{"date": "2026-06-15", "spend_cop": 120000, "conversations_started": 80, "total_orders": 12,
              "metrics": {"drop_off_rate": "0.40", "mer": "5.0"}, "diagnosis": {"recommendation": "scale"}}],
    "period": {"metrics": {"mer": "5.0", "drop_off_rate": "0.40"}, "diagnosis": {"recommendation": "scale"}},
    "unmatched": {"meta_only": [], "sales_only": []}, "qa_passed": True,
    "campaigns": [{"campaign_name": "Día del padre", "spend_cop": 120000, "link_clicks": 80,
                   "conversations": 120, "conversation_source": "insights"}],
}


def test_real_deepseek_narrative_cites_only_known_numbers():
    from graphs.ctwa_report import _narrate_user, build, invented_numbers
    from sdk.connectorkit.ports import LiteLLMProxy

    state = build(llm=LiteLLMProxy()).invoke(REPORT)
    narrative = state["narrative"]
    assert narrative and len(narrative) > 20  # produjo prosa
    # lo que hace CONFIABLE al nodo LLM: NO alucinó ningún número financiero
    assert invented_numbers(narrative, _narrate_user(REPORT)) == [], narrative
