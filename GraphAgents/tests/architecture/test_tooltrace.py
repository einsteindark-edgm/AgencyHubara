"""El KIT de tracing — `ToolLedger` + `traced()` + `replay_with_trace()`. La unidad de
observabilidad por-tool: envuelve el mapping `tools` que el loader inyecta a `run()` para
registrar, EN ORDEN, qué recibió y devolvió cada tool. DETERMINISTA (seq ordinal, sin
timestamps) → es la base de la reconstrucción del I/O por-tool del viewer (G-DET: el
supervisor durable corre el MISMO `run()`, así que replayearlo reproduce lo que pasó).
"""
from __future__ import annotations

from pathlib import Path

from sdk.case_model import discover_cases, resolve
from sdk.manifest_model import load_manifest
from sdk.tooltrace import ToolLedger, replay_with_trace, traced

ROOT = Path(__file__).resolve().parents[2]  # .../GraphAgents
MANIFESTS = ROOT / "manifests"


def test_traced_records_each_call_in_order_with_io():
    led = ToolLedger()
    tools = {"a": lambda *, payload: {"x": payload + 1}, "b": lambda *, payload: {"y": payload * 2}}
    t = traced(tools, led)
    t["a"](payload=1)
    t["b"](payload=3)
    assert led.entries == [
        {"seq": 0, "tool": "a", "input": {"payload": 1}, "output": {"x": 2}},
        {"seq": 1, "tool": "b", "input": {"payload": 3}, "output": {"y": 6}},
    ]


def test_traced_is_transparent_returns_the_real_output():
    led = ToolLedger()
    t = traced({"a": lambda *, payload: payload * 10}, led)
    assert t["a"](payload=5) == 50  # el wrapper NO altera el output — la impl no sabe que la trazan


def test_replay_with_trace_reconstructs_the_real_per_tool_io():
    """La reconstrucción: corre `run()` de la capability con tools TRAZADAS sobre un input real
    → devuelve el output de dominio + el I/O exacto de cada tool, en orden. Es lo que el viewer
    usa para mostrar qué entró/salió de cada tool de un nodo (replay determinista, G-DET)."""
    seed = resolve(next(c for c in discover_cases(ROOT) if c.id == "dia-del-padre-flujo").seed, ROOT)
    node = load_manifest(MANIFESTS / "ctwa-campaign-funnel.agent.yaml")
    res = replay_with_trace(
        node, ROOT, {"entities_payload": seed["entities_payload"], "insights_payload": seed["meta_insights"]}
    )
    assert [e["tool"] for e in res["tool_trace"]] == [
        "parse-meta-entities", "meta-ads-insights", "complement-funnel"]
    # cada entry lleva el I/O REAL de esa tool:
    assert res["tool_trace"][0]["input"]["payload"] == seed["entities_payload"]
    assert "campaigns" in res["tool_trace"][0]["output"]            # parse-entities → {campaigns: [...]}
    assert res["output"] == res["tool_trace"][-1]["output"]         # el output de la capability == el del último tool
    assert [e["seq"] for e in res["tool_trace"]] == [0, 1, 2]       # orden ordinal, determinista


def test_replay_with_trace_on_a_reference_resolves_the_real_agent():
    """Un nodo REFERENCIA (`uses: agent://...`, como los miembros de un supervisor) se resuelve al
    agente real antes de replayear — así el viewer reconstruye el trace de un sub-agente del pod
    por su id, desde el plan del supervisor (que lleva referencias, no leaves)."""
    seed = resolve(next(c for c in discover_cases(ROOT) if c.id == "dia-del-padre-flujo").seed, ROOT)
    sup = load_manifest(MANIFESTS / "ads-analytics.taskgraph.yaml")
    ref = next(a for a in sup.agents if (a.ref_agent_id or a.name) == "ctwa-insights")
    assert ref.is_reference  # `uses: agent://ctwa-insights@1` — no es una capability leaf
    res = replay_with_trace(ref, ROOT, {"payload": seed["meta_insights"]})
    assert [e["tool"] for e in res["tool_trace"]] == ["meta-ads-insights"]


def test_replay_flow_with_trace_expone_el_acc_por_nodo():
    """`node_accs`: el snapshot del acumulador DESPUÉS de cada nodo del pod —
    lo que el Inspector muestra como «salió» de un nodo en una ejecución LOCAL
    (sin Conductor no hay /api/node-state; el replay determinista lo repone).
    El acc del último nodo ES el output final del pod."""
    from sdk.tooltrace import replay_flow_with_trace

    case = next(c for c in discover_cases(ROOT) if c.id == "dia-del-padre-flujo")
    seed = resolve(case.seed, ROOT)
    sup = load_manifest(MANIFESTS / "ads-analytics.taskgraph.yaml")
    from sdk.replay import build_ports

    res = replay_flow_with_trace(sup, ROOT, seed, ports=build_ports(case, ROOT))
    assert res["node_traces"], "el pod secuencial debe traer ledger por nodo"
    accs = res["node_accs"]
    # un acc por miembro del pod, con las MISMAS claves que node_traces
    assert set(accs.keys()) == set(res["node_traces"].keys())
    # el acc se ACUMULA: el del último nodo es exactamente el output final
    labels = [a.ref_agent_id or a.name for a in sup.agents]
    assert accs[labels[-1]] == res["output"]
    # y el de un nodo intermedio ya contiene lo sembrado + lo suyo, pero no el final
    assert isinstance(accs[labels[0]], dict)
