"""El nodo LLM narrativo del `ctwa-report` (G1.x). El LLM va en un nodo MARCADO; el golden se
replayea con `FixtureLLM` (determinista) → G-DET se sostiene. Lo que se asierta:
- narrate() teje la prosa que devuelve el LLM y le PASA los números del analyzer al prompt;
- el guard anti-alucinación caza un número inventado (lo que hace confiable al nodo LLM);
- build() agrega el nodo narrativo después del render determinista.
"""
from __future__ import annotations

import pytest

from graphs.ctwa_report import invented_numbers, narrate
from sdk.connectorkit.ports import FixtureLLM

REPORT = {
    "days": [
        {"date": "2026-06-15", "spend_cop": 120000, "conversations_started": 80, "total_orders": 12,
         "metrics": {"drop_off_rate": "0.40", "mer": "5.0"}, "diagnosis": {"recommendation": "scale"}},
    ],
    "period": {"metrics": {"mer": "5.0", "drop_off_rate": "0.40"},
               "diagnosis": {"recommendation": "scale"}},
    "unmatched": {"meta_only": [], "sales_only": []},
    "qa_passed": True,
    "campaigns": [{"campaign_name": "Día del padre", "spend_cop": 120000, "link_clicks": 80,
                   "conversations": 120, "conversation_source": "insights"}],
}


def test_narrate_returns_the_llm_prose():
    out = narrate(REPORT, llm=FixtureLLM("El periodo recomienda scale con MER 5.0."))
    assert out == {"narrative": "El periodo recomienda scale con MER 5.0."}


def test_narrate_feeds_the_analyzer_numbers_to_the_prompt():
    # FixtureLLM que ECHO-ea el prompt → podemos asertar QUÉ números recibió el LLM
    out = narrate(REPORT, llm=FixtureLLM(lambda user: user))
    prompt = out["narrative"]
    assert "scale" in prompt and "120000" in prompt and "MER del periodo: 5.0" in prompt


def test_guard_passes_when_narrative_cites_only_known_numbers():
    src = _narrate_source(REPORT)
    narrative = "El periodo recomienda scale: con spend de 120000 y 120 conversaciones, MER 5.0."
    assert invented_numbers(narrative, src) == []


def test_guard_flags_an_invented_number():
    src = _narrate_source(REPORT)
    narrative = "Gastaste 120000 pero generaste 999999 en ventas."  # 999999 no está en la fuente
    assert "999999" in invented_numbers(narrative, src)


def test_guard_catches_collision_invention():
    """El guard NO debe dejar pasar un inventado solo porque su forma sin-puntos colisiona con
    otro número de distinto CONCEPTO: 'drop-off 50%' es inventado (el real es 0.40), aunque
    '50' coincida con la forma sin-punto de MER 5.0. Hay que comparar VALORES, no dígitos."""
    src = _narrate_source(REPORT)
    assert invented_numbers("El drop-off real fue del 50%.", src)  # 50 es inventado


def test_guard_allows_percentage_reformat_of_a_ratio():
    """Citar el ratio 0.40 como '40%' es legítimo (mismo número, otra forma) — no inventado."""
    src = _narrate_source(REPORT)
    assert invented_numbers("El drop-off fue de 40%.", src) == []


def test_guard_allows_european_thousands_format():
    """120000 citado como '120.000,00' (formato europeo) es el mismo valor — no inventado."""
    src = _narrate_source(REPORT)
    assert invented_numbers("Gastaste 120.000,00 en total.", src) == []


def test_build_adds_the_narrative_node_after_the_render():
    pytest.importorskip("langgraph")
    from graphs.ctwa_report import build
    graph = build(llm=FixtureLLM("Narrativa de prueba."))
    state = graph.invoke(REPORT)
    assert "Embudo por campaña" in state["markdown"]          # el render determinista (run)
    assert state["narrative"] == "Narrativa de prueba."        # el nodo LLM


def _narrate_source(report: dict) -> str:
    from graphs.ctwa_report import _narrate_user
    return _narrate_user(report)
