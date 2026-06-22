"""CONFORMANCE del protocolo Capability — la prueba BEHAVIORAL que AMARRA cada capability a su
manifest. Replaya el pod REAL (dia-del-padre) con tools trazadas y exige que las tools que CADA
nodo INVOCA (id + orden) == las que DECLARA. Es lo que hace HONESTA la sub-ejecución del viewer:
el orden que pinta no es 'lo declarado' sino 'lo que CORRE', reconstruido por replay determinista
(G-DET). Y el protocolo estructural (G-PROTO) queda integrado a la cert (run_checks/level_of).
"""
from __future__ import annotations

from pathlib import Path

from sdk.case_model import discover_cases, resolve
from sdk.graph import _tools_map
from sdk.manifest_model import load_manifest
from sdk.testkit.checks import check_capability_conforms_protocol, run_checks
from sdk.tooltrace import replay_flow_with_trace

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = ROOT / "manifests"
POD_NODES = {"ctwa-insights", "sales-ledger", "ctwa-campaign-funnel",
             "blended-economics", "numbers-qa", "ctwa-report"}


def _seed(case_id: str) -> dict:
    return resolve(next(c for c in discover_cases(ROOT) if c.id == case_id).seed, ROOT)


def _distinct_in_order(ids: list[str]) -> list[str]:
    seen: list[str] = []
    for t in ids:
        if t not in seen:
            seen.append(t)
    return seen


def test_pod_replay_proves_every_node_invokes_its_declared_tools_in_order():
    """EL AMARRE: replayeando el pod real con tools trazadas, las tools DISTINTAS que CADA nodo
    invoca (en orden de PRIMERA llamada) == las que declara su manifest. Garantiza dos cosas: (1)
    ningún nodo llama una tool que no declaró (el manifest no miente), (2) el orden declarado ==
    el orden real — lo que el viewer pinta como sub-ejecución. Un nodo puede LLAMAR una tool en
    loop (el trace real la repite); el manifest declara la COMPOSICIÓN única → distinct-in-order
    reconcilia ambas verdades. Si una capability llamara una tool NO declarada, rojo acá."""
    sup = load_manifest(MANIFESTS / "ads-analytics.taskgraph.yaml")
    res = replay_flow_with_trace(sup, ROOT, _seed("dia-del-padre-flujo"))
    declared = _tools_map(ROOT)
    assert set(res["node_traces"]) == POD_NODES
    for agent, entries in res["node_traces"].items():
        real = _distinct_in_order([e["tool"] for e in entries])
        assert real == declared.get(agent, []), f"{agent}: distinct(real)={real} != declarado={declared.get(agent)}"
    # el nodo multi-tool sin loop: el trace real == declarado, 1:1 (lo que pinta el viewer):
    assert [e["tool"] for e in res["node_traces"]["ctwa-campaign-funnel"]] == [
        "parse-meta-entities", "meta-ads-insights", "complement-funnel"]


def test_reconstructed_trace_reveals_the_real_call_pattern_incl_loops():
    """El trace reconstruido es MÁS rico que el manifest: revela el patrón REAL de llamadas,
    incluidos LOOPS. blended-economics llama blended-unit-economics + diagnose POR-DÍA (y una vez
    para el período agregado) → el trace lo muestra repetido, aunque el manifest declara cada tool
    UNA vez. Esa es la diferencia honesta entre 'composición declarada' (el panel) y 'ejecución
    real' (el modal reconstruido) — el modal muestra MÁS, no miente."""
    sup = load_manifest(MANIFESTS / "ads-analytics.taskgraph.yaml")
    res = replay_flow_with_trace(sup, ROOT, _seed("dia-del-padre-flujo"))
    blended = res["node_traces"]["blended-economics"]
    bue = [e for e in blended if e["tool"] == "blended-unit-economics"]
    assert len(bue) >= 2, "blended-unit-economics se llama por-día en loop → el trace debe repetirla"
    assert blended[0]["tool"] == "merge-by-date"  # el merge una sola vez, primero
    assert _distinct_in_order([e["tool"] for e in blended]) == [
        "merge-by-date", "blended-unit-economics", "diagnose"]


def test_each_traced_entry_carries_real_io():
    """Cada entry del trace lleva el I/O REAL de esa tool — lo que el viewer muestra en input/output."""
    sup = load_manifest(MANIFESTS / "ads-analytics.taskgraph.yaml")
    res = replay_flow_with_trace(sup, ROOT, _seed("dia-del-padre-flujo"))
    funnel = res["node_traces"]["ctwa-campaign-funnel"]
    assert funnel[0]["tool"] == "parse-meta-entities" and "campaigns" in funnel[0]["output"]
    assert funnel[-1]["tool"] == "complement-funnel" and isinstance(funnel[-1]["output"], dict)


def test_traced_flow_replay_matches_the_untraced_golden_output():
    """El tracing es TRANSPARENTE a nivel flujo: el output del pod replayeado-con-trace == el
    golden (lo que produce la corrida sin trazar). Reconstruir el I/O no altera el resultado."""
    sup = load_manifest(MANIFESTS / "ads-analytics.taskgraph.yaml")
    res = replay_flow_with_trace(sup, ROOT, _seed("dia-del-padre-flujo"))
    golden = resolve(next(c for c in discover_cases(ROOT) if c.id == "dia-del-padre-flujo").golden, ROOT)
    for k, v in golden.items():
        assert res["output"][k] == v, f"el output divergió en '{k}'"


def test_capability_protocol_is_part_of_certification():
    """G-PROTO: el protocolo estructural está integrado a run_checks (level_of) — CADA capability
    leaf del catálogo conforma (el check corre por-agente, como el resto de la cert)."""
    leaves = sorted(MANIFESTS.glob("*.agent.yaml"))
    assert leaves  # hay capabilities que certificar
    for p in leaves:
        root = load_manifest(p)
        assert check_capability_conforms_protocol(root) == [], p.name
        assert not any("G-PROTO" in e for e in run_checks(root, ROOT)["errors"]), p.name


def test_g_proto_surfaces_a_nonconforming_capability(monkeypatch):
    """Caso NEGATIVO (regla de oro): si una capability no conforma, G-PROTO lo caza en run_checks
    (el check delega en assert_capability — fuente única del contrato). Corre sobre un LEAF (el
    supervisor referencia agentes; las capabilities viven en los *.agent.yaml)."""
    import sdk.capability

    monkeypatch.setattr(sdk.capability, "assert_capability", lambda m: ["[G-PROTO] boom"])
    root = load_manifest(MANIFESTS / "ctwa-campaign-funnel.agent.yaml")
    assert any("G-PROTO" in e for e in run_checks(root, ROOT)["errors"])
