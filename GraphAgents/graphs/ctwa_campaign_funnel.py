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


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    if "entities_payload" not in input or "insights_payload" not in input:
        raise ValueError(
            "ctwa-campaign-funnel: faltan 'entities_payload' (ads_get_ad_entities) y/o "
            "'insights_payload' (Graph /insights) — los deposita el central"
        )
    tools = tools or {}
    parse_entities = tools["parse-meta-entities"]
    parse_insights = tools["meta-ads-insights"]
    complement = tools["complement-funnel"]

    entities = parse_entities(payload=input["entities_payload"])["campaigns"]
    insights = parse_insights(payload=input["insights_payload"])["insights"]
    return complement(payload={"entities": entities, "insights": insights})


def build():
    try:
        from langgraph.graph import StateGraph  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e
    raise NotImplementedError("build(): cablear el StateGraph (G1+); el run puro ya está")
