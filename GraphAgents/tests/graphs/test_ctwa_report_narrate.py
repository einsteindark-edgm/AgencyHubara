"""El nodo LLM narrativo del `ctwa-report` (G1.x). El LLM va en un nodo MARCADO; el golden se
replayea con `FixtureLLM` (determinista) → G-DET se sostiene. Lo que se asierta:
- narrate() teje la prosa que devuelve el LLM y le PASA los números del analyzer al prompt;
- el guard anti-alucinación caza un número inventado (lo que hace confiable al nodo LLM);
- build() agrega el nodo narrativo después del render determinista.
"""
from __future__ import annotations

import pytest

from graphs.ctwa_report import invented_numbers, narrate, run  # noqa: F401  (run usado en los tests del flujo)
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


def test_narrate_prompt_rounds_long_decimal_tails():
    """Los ratios del analyzer llegan como Decimal serializado con cola larga (run real prod:
    MER '0.7860605266605528625704179222'). El prompt del LLM los redondea a 2 decimales — el LLM
    cita lo que recibe, así que sin esto la prosa repite la cola ilegible al cliente."""
    report = {
        **REPORT,
        "period": {"metrics": {"mer": "0.7860605266605528625704179222",
                               "drop_off_rate": "0.8035714285714285714285714286"},
                   "diagnosis": {"recommendation": "rotate_creative"}},
    }
    out = narrate(report, llm=FixtureLLM(lambda user: user))
    prompt = out["narrative"]
    assert "MER del periodo: 0.79" in prompt
    assert "drop-off del periodo: 0.8" in prompt
    assert "0.7860605266605528" not in prompt


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


# --- run() cablea el LLM por el PORT (patrón meta-insights) — la narrativa entra al FLUJO ----

def test_run_adds_the_narrative_when_the_llm_port_is_injected():
    """El cable al flujo: con el port `llm` inyectado (lo hace el loader vía consumes:), run()
    produce la narrativa ADEMÁS del render determinista. Es lo que el supervisor del pod corre
    (build_runnable→run), así el nodo LLM por fin se ejecuta en el flujo."""
    out = run(REPORT, ports={"llm": FixtureLLM("El periodo recomienda scale con MER 5.0.")})
    assert out["narrative"] == "El periodo recomienda scale con MER 5.0."
    assert "Embudo por campaña" in out["markdown"]  # el render determinista sigue intacto


def test_run_stays_pure_without_the_llm_port():
    """Sin port → sin narrativa: el golden determinista del render NO cambia (G-DET; el port es
    opt-in, igual que el build() previo). El narrative aparece solo cuando se inyecta el LLM."""
    out = run(REPORT)
    assert "narrative" not in out


def test_run_drops_a_hallucinated_narrative():
    """SEGURIDAD del LLM real: si la narrativa cita una cifra financiera ausente de los números
    del analyzer, run() la DESCARTA (no la sirve como si fuera confiable) — el guard invented_numbers
    gobierna. El cliente nunca ve un número inventado por el LLM."""
    out = run(REPORT, ports={"llm": FixtureLLM("Gastaste 120000 y generaste 999999 en ventas.")})
    assert "999999" not in out["narrative"]
    assert "descart" in out["narrative"].lower()  # marca que la narrativa no es confiable


class _UnreachableLLM:
    """Simula el proxy LiteLLM inalcanzable del run real en AWS: vive en OTRA caja EC2, así que
    `localhost:4000` desde la caja de GraphAgents revienta con `[Errno 99] Cannot assign requested
    address`. El port levanta la MISMA excepción que `urllib` (URLError envolviendo el OSError)."""

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        import urllib.error

        raise urllib.error.URLError(OSError(99, "Cannot assign requested address"))


def test_run_survives_an_unreachable_llm_proxy():
    """Run real en AWS (PR #81): el proxy LiteLLM está en otra caja → el nodo narrativo pegó a
    localhost:4000 y reventó con [Errno 99], tumbando el `ctwa_report` ENTERO. Una narrativa
    OPCIONAL no debe matar el reporte determinista (G-DET: el render es PURO, no toca red). run()
    emite el reporte igual, con un marcador VISIBLE de narrativa no disponible + el error en
    `narrative_error` (trace/observabilidad). El workflow COMPLETA en vez de FALLAR (L-26)."""
    out = run(REPORT, ports={"llm": _UnreachableLLM()})
    # el deliverable determinista sigue intacto (no se perdió por la falla de un nodo IO opcional)
    assert "Embudo por campaña" in out["markdown"]
    assert out["verdict"] == "scale"
    # la narrativa degrada VISIBLE — no crash, no silencio
    assert "no disponible" in out["narrative"].lower()
    # el error queda en el trace como diagnóstico (no se traga: se reubica de fatal a observable)
    assert "99" in out["narrative_error"]
    # no hay narrative_invented: no se produjo prosa que chequear (distingue 'inalcanzable' de 'alucinó')
    assert "narrative_invented" not in out


def test_narrative_error_never_leaks_the_api_key(monkeypatch):
    """SEGURIDAD (regresión, opción D): cuando el LLM DIRECTO autenticado (Bearer) falla, el
    `narrative_error` que viaja al trace/explorer NO debe contener la API key. Hoy es seguro por
    construcción (urllib no echa los request headers al str del error); este guard lo BLINDA si
    alguien algún día cambia la lib HTTP por una que sí los eche. Usa el vendor REAL + el error
    realista de L-26 (Errno 99)."""
    import urllib.error

    from sdk.connectorkit.ports import LiteLLMProxy

    def boom(req, timeout=None):
        raise urllib.error.URLError(OSError(99, "Cannot assign requested address"))

    monkeypatch.setattr("urllib.request.urlopen", boom)
    out = run(REPORT, ports={"llm": LiteLLMProxy(
        base_url="https://api.deepseek.com", api_key="sk-CANARY-LEAK")})
    assert "no disponible" in out["narrative"].lower()       # degradó visible (PR #89)
    assert "sk-CANARY-LEAK" not in out["narrative_error"]    # la key NO viaja al trace
    assert "sk-CANARY-LEAK" not in out["narrative"]          # ni al cliente


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
