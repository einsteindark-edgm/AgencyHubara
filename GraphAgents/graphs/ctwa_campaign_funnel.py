"""Capability `ctwa-campaign-funnel` (analyzer) — el embudo POR CAMPAÑA, con la
conversación COMPLEMENTADA. Compone 3 tools del catálogo (G-DET, esqueleto puro):

  parse-meta-entities  (clasifica por objetivo: messaging / sales / traffic)
  + meta-ads-insights  (la conversación real de Graph /insights `actions`)
  + complement-funnel  (las une por campaign_id → recupera la conversación que el
                        MCP entities da en 0 para purchase-conversion)

Recibe DOS JSONs (los deposita el central): el de `ads_get_ad_entities` y el de
`/insights`. El agente no llama a Meta. Ver docs/meta-ctwa-data-acquisition.md.

- run(input, *, ports, tools) — PURA (G-RUN-SIG, golden-replay).
- build()                     — StateGraph LangGraph (G1+).
"""
from __future__ import annotations


def _require_payloads(d: dict) -> None:
    """Guard de DOMINIO del seam central→agente (MF-7, ver L-11): si falta un payload, un
    error accionable — no un KeyError crudo. Vive UNA vez → corre idéntico en `run()` y en
    el nodo `validate` del StateGraph (sin drift run↔build)."""
    if "entities_payload" not in d or "insights_payload" not in d:
        raise ValueError(
            "ctwa-campaign-funnel: faltan 'entities_payload' (ads_get_ad_entities) y/o "
            "'insights_payload' (Graph /insights) — los deposita el central"
        )


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    _require_payloads(input)
    tools = tools or {}
    parse_entities = tools["parse-meta-entities"]
    parse_insights = tools["meta-ads-insights"]
    complement = tools["complement-funnel"]

    entities = parse_entities(payload=input["entities_payload"])["campaigns"]
    insights = parse_insights(payload=input["insights_payload"])["insights"]
    return complement(payload={"entities": entities, "insights": insights})


def build(*, checkpointer=None):
    """`StateGraph` LangGraph — el embudo como task graph EXPLÍCITO: un nodo por tool, en
    cadena (parse-entities → parse-insights → complement). Los nodos REUSAN las impls puras
    del catálogo (las mismas que compone el `run()`) — la lógica vive UNA vez (G-DET); el
    grafo solo las cablea, y es la estructura que se ve en langgraph Studio.

    DURABILIDAD (honesto, ver L-11): pasá un `checkpointer` (LangGraph: MemorySaver / SQLite
    / Postgres) → recovery POR-NODO real cuando LangGraph drive la ejecución: si un nodo
    crashea, al reanudar (mismo `thread_id`) re-corre SOLO ese nodo, los previos se cargan del
    checkpoint (probado en `tests/integration/test_durable_recovery.py`). SIN checkpointer
    (default), o por el path passthrough de AgentSpan, el grafo corre como UNA task (recovery
    del run completo). La compilación a tasks por-nodo NATIVA de AgentSpan es G2.
    `compile(name=...)` es el nombre que lee AgentSpan."""
    try:
        from typing import TypedDict

        from langgraph.graph import END, START, StateGraph
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e

    from tools.complement_funnel.impl import run as complement
    from tools.meta_ads_insights.impl import run as parse_insights
    from tools.parse_meta_entities.impl import run as parse_entities

    class State(TypedDict, total=False):
        entities_payload: object   # envelope crudo de ads_get_ad_entities (lo deposita el central)
        insights_payload: object   # payload de Graph /insights (lo deposita el central)
        entities: list             # ← parse-entities
        insights: list             # ← meta-ads-insights
        campaigns: list            # ← complement (el embudo final)
    # Nota G-STATE: hoy la cadena es SECUENCIAL y cada nodo escribe una clave distinta
    # (sin writes concurrentes) → no necesita reducers. Si se paraleliza el fan-out de los
    # dos parsers (START→parse_entities ∥ START→parse_insights), declarar reducers (G-STATE).

    def validate_node(state: State) -> dict:
        _require_payloads(state)  # mismo guard de dominio que run() (paridad run↔build, L-11)
        return {}

    def parse_entities_node(state: State) -> dict:
        return {"entities": parse_entities(payload=state["entities_payload"])["campaigns"]}

    def parse_insights_node(state: State) -> dict:
        return {"insights": parse_insights(payload=state["insights_payload"])["insights"]}

    def complement_node(state: State) -> dict:
        return complement(payload={"entities": state["entities"], "insights": state["insights"]})

    g = StateGraph(State)
    g.add_node("validate", validate_node)
    g.add_node("parse_entities", parse_entities_node)
    g.add_node("parse_insights", parse_insights_node)
    g.add_node("complement", complement_node)
    g.add_edge(START, "validate")
    g.add_edge("validate", "parse_entities")
    g.add_edge("parse_entities", "parse_insights")
    g.add_edge("parse_insights", "complement")
    g.add_edge("complement", END)
    return g.compile(name="ctwa-campaign-funnel", checkpointer=checkpointer)
