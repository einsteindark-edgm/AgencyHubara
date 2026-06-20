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
