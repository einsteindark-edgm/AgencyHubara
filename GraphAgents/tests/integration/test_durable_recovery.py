"""G1.x · recovery POR-NODO real — el test de crash mid-graph que L-11 exige.

L-11 dejó la regla: un claim de recovery por-nodo SOLO vale si hay un test que mate el
proceso a mitad del grafo y pruebe que NO recomputa los nodos previos. Esto es ese test.

Mecanismo: un `StateGraph` multi-nodo compilado con un **checkpointer** (LangGraph) persiste
el estado después de cada super-step. Si un nodo crashea, el checkpoint guarda hasta el último
nodo OK; al reanudar (mismo `thread_id`) re-corre SOLO el nodo que falló — los previos se
cargan del checkpoint, no se recomputan.

Honesto (alcance): esto prueba el recovery por-nodo cuando LangGraph DRIVE la ejecución (la
historia de durabilidad del LocalRuntime/checkpointer). El passthrough de AgentSpan sigue
corriendo el grafo entero como UNA task (la compilación a tasks por-nodo NATIVA de AgentSpan
es G2). Skipea sin langgraph.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

GA = Path(__file__).resolve().parents[2]
ENT = GA / "fixtures" / "mcp_ad_entities.json"
INS = GA / "fixtures" / "meta_insights_campaigns.json"


def _seed() -> dict:
    return {
        "entities_payload": json.loads(ENT.read_text(encoding="utf-8")),
        "insights_payload": json.loads(INS.read_text(encoding="utf-8")),
    }


_SUP_SALES = {"sales": [{"date": "2026-06-15", "total_orders": 12, "total_revenue": 600000}]}


def _sup_seed() -> dict:
    return {
        "meta_insights": json.loads(INS.read_text(encoding="utf-8")),
        "manual_sales": _SUP_SALES,
        "entities_payload": json.loads(ENT.read_text(encoding="utf-8")),
    }


def test_funnel_recupera_por_nodo_sin_recomputar(monkeypatch) -> None:
    from langgraph.checkpoint.memory import MemorySaver

    import tools.complement_funnel.impl as cf
    import tools.parse_meta_entities.impl as pe
    from graphs.ctwa_campaign_funnel import build

    calls = {"parse_entities": 0, "complement": 0}
    real_pe, real_cf = pe.run, cf.run

    def counting_pe(*, payload):
        calls["parse_entities"] += 1
        return real_pe(payload=payload)

    def flaky_cf(*, payload):
        # crashea la PRIMERA vez (simula que el proceso muere justo en complement),
        # tiene éxito al reanudar.
        calls["complement"] += 1
        if calls["complement"] == 1:
            raise RuntimeError("crash mid-graph (simulado) antes de complement")
        return real_cf(payload=payload)

    monkeypatch.setattr(pe, "run", counting_pe)
    monkeypatch.setattr(cf, "run", flaky_cf)

    # build() DESPUÉS del patch → sus imports toman las versiones instrumentadas.
    graph = build(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "crash-recovery-1"}}

    # 1) primer intento: crashea en complement (validate+parse_entities+parse_insights ya
    #    quedaron en el checkpoint).
    with pytest.raises(Exception):
        graph.invoke(_seed(), config)

    # 2) reanudar con el MISMO thread_id: re-corre SOLO complement, no los previos.
    out = graph.invoke(None, config)

    # LA PRUEBA de recovery por-nodo: parse-entities corrió UNA vez (no se recomputó en el
    # resume); complement corrió dos (falló + éxito).
    assert calls["parse_entities"] == 1, "parse-entities se RECOMPUTÓ al reanudar (no hubo recovery por-nodo)"
    assert calls["complement"] == 2

    # y el resultado correcto sobrevive el crash+resume: "Día del padre" recupera 120.
    padre = {c["campaign_id"]: c for c in out["campaigns"]}["120243118818600317"]
    assert padre["conversations"] == 120
    assert padre["conversation_source"] == "insights"


def test_supervisor_compuesto_recupera_por_agente(monkeypatch) -> None:
    """G2 + L-11/L-14: el supervisor compuesto (build_supervisor_graph) con un checkpointer
    recupera POR-AGENTE — crasheo blended-economics (agente 4) y pruebo que sales-ledger
    (agente 2) NO se recomputó al reanudar. Esto EJERCITA el claim de durabilidad del grafo
    compuesto (sin esto, sería un overclaim — la regla de L-11)."""
    from langgraph.checkpoint.memory import MemorySaver

    import tools.merge_by_date.impl as mbd
    import tools.parse_manual_sales.impl as pms
    from sdk.loader import build_supervisor_graph
    from sdk.manifest_model import load_manifest

    calls = {"parse_manual_sales": 0, "merge_by_date": 0}
    real_pms, real_mbd = pms.run, mbd.run

    def counting_pms(*, payload):  # sales-ledger (agente 2)
        calls["parse_manual_sales"] += 1
        return real_pms(payload=payload)

    def flaky_mbd(*, payload):  # blended-economics (agente 4) crashea la 1ra vez
        calls["merge_by_date"] += 1
        if calls["merge_by_date"] == 1:
            raise RuntimeError("crash mid-supervisor en blended-economics (agente 4)")
        return real_mbd(payload=payload)

    monkeypatch.setattr(pms, "run", counting_pms)
    monkeypatch.setattr(mbd, "run", flaky_mbd)

    manifest = load_manifest(GA / "manifests" / "ads-analytics.taskgraph.yaml")
    graph = build_supervisor_graph(manifest, GA, checkpointer=MemorySaver())  # build DESPUÉS del patch
    config = {"configurable": {"thread_id": "sup-crash-recovery-1"}}

    with pytest.raises(Exception):
        graph.invoke({"acc": _sup_seed()}, config)  # crashea en el agente 4

    out = graph.invoke(None, config)  # reanuda: re-corre SOLO desde el agente 4

    # LA PRUEBA de recovery por-AGENTE: sales-ledger (agente 2) corrió UNA vez (no se
    # recomputó); merge-by-date (agente 4) corrió dos (falló + éxito).
    assert calls["parse_manual_sales"] == 1, "sales-ledger se RECOMPUTÓ al reanudar (sin recovery por-agente)"
    assert calls["merge_by_date"] == 2
    assert "Embudo por campaña" in out["acc"]["markdown"]  # el pod completa tras el resume
