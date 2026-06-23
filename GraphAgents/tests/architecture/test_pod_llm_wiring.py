"""El CABLE del nodo LLM al FLUJO: `ctwa-report` `consumes: llm` → (1) el grafo muestra el edge
(`port:llm` deja de ser un nodo huérfano en el explorer) y (2) el pod, corrido con el port
inyectado, teje la narrativa por el reporter. Determinista (FixtureLLM) — la verificación con el
LLM REAL (deepseek vía el proxy) vive en `tests/integration/test_llm_narrative.py`.
"""
from __future__ import annotations

from pathlib import Path

from sdk.case_model import discover_cases, resolve
from sdk.connectorkit.ports import FixtureLLM
from sdk.graph import build_graph
from sdk.loader import build_runnable
from sdk.manifest_model import load_manifest

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = ROOT / "manifests"


def test_report_consumes_the_llm_port_so_it_is_wired_in_the_graph():
    g = build_graph(ROOT)
    edges = {(e["source"], e["target"], e["kind"]) for e in g["edges"]}
    assert ("agent:ctwa-report", "port:llm", "consumes") in edges  # el cable VISIBLE en el explorer
    connected = {x for e in g["edges"] for x in (e["source"], e["target"])}
    assert "port:llm" in connected  # port:llm ya NO es huérfano


def test_pod_weaves_the_narrative_when_the_llm_port_is_injected():
    """El pod COMPLETO (LocalRuntime) con el port `llm` → el reporter teje la narrativa. Exercita
    consumes:llm (manifest) + run() (el port) + el threading de ports del supervisor."""
    seed = resolve(next(c for c in discover_cases(ROOT) if c.id == "dia-del-padre-flujo").seed, ROOT)
    sup = load_manifest(MANIFESTS / "ads-analytics.taskgraph.yaml")
    out = build_runnable(sup, ROOT, ports={"llm": FixtureLLM("El periodo recomienda rotar el creativo.")})(seed)
    assert out["narrative"] == "El periodo recomienda rotar el creativo."  # tejida EN el flujo
    assert out["verdict"]  # el render determinista sigue


def test_pod_stays_pure_without_the_llm_port():
    """Sin port inyectado, el pod NO produce narrativa (el golden del pod no cambia; opt-in)."""
    seed = resolve(next(c for c in discover_cases(ROOT) if c.id == "dia-del-padre-flujo").seed, ROOT)
    sup = load_manifest(MANIFESTS / "ads-analytics.taskgraph.yaml")
    out = build_runnable(sup, ROOT)(seed)
    assert "narrative" not in out


def test_durable_supervisor_threads_the_llm_port_to_the_reporter():
    """El path DURABLE (build_supervisor_graph compila el pod a un StateGraph que corre AgentSpan)
    debe THREAD-ear los ports a sus miembros → el reporter teje la narrativa también en el durable.
    El LocalRuntime ya lo hacía (build_runnable pasa ports); el durable no — éste es el cable nuevo."""
    import pytest

    pytest.importorskip("langgraph")
    from sdk.connectorkit.ports import FixtureLLM as _Fx
    from sdk.loader import build_supervisor_graph

    seed = resolve(next(c for c in discover_cases(ROOT) if c.id == "dia-del-padre-flujo").seed, ROOT)
    g = build_supervisor_graph(load_manifest(MANIFESTS / "ads-analytics.taskgraph.yaml"), ROOT,
                               ports={"llm": _Fx("Rotá el creativo del Día del padre.")})
    out = g.invoke({"acc": seed})
    assert out["acc"]["narrative"] == "Rotá el creativo del Día del padre."
