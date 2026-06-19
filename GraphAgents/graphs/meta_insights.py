"""Capability `meta-insights` (extractor). Esqueleto PURO (G-DET): extract via el
port del ConnectorKit + normalize. Dos entrypoints (convención de capability):

- `run(input, *, ports, tools)` — PURA, para el LocalRuntime / golden-replay.
- `build()`                     — el StateGraph LangGraph (adapter al runtime real).

La firma de `run` es uniforme (`*, ports, tools`) aunque solo use `ports`: el
loader inyecta ambos (ver L-1).
"""
from __future__ import annotations


def _normalize(rows: list[dict]) -> dict:
    campaigns = []
    for r in rows:
        spend = float(r.get("spend", 0.0))
        revenue = float(r.get("revenue", 0.0))
        campaigns.append(
            {
                "campaign_id": r["campaign_id"],
                "spend": round(spend, 2),
                "revenue": round(revenue, 2),
                "roas": round(revenue / spend, 4) if spend else 0.0,
            }
        )
    total_spend = round(sum(c["spend"] for c in campaigns), 2)
    total_revenue = round(sum(c["revenue"] for c in campaigns), 2)
    return {
        "campaigns": campaigns,
        "total_spend": total_spend,
        "total_revenue": total_revenue,
        "blended_roas": round(total_revenue / total_spend, 4) if total_spend else 0.0,
    }


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    ports = ports or {}
    port = ports["meta_marketing_api"]  # G-PORT: datos SOLO por el ConnectorKit
    rows = port.fetch(account_id=input["account_id"], since=input["since"], until=input["until"])
    return _normalize(rows)


def build():
    """StateGraph LangGraph (adapter al runtime real). Requiere langgraph."""
    try:
        from langgraph.graph import END, START, StateGraph  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e
    # G1+: ensamblar extract/normalize como nodos del StateGraph reusando los puros
    # de arriba (el `run` puro ya es la lógica; acá solo va el cableado del grafo).
    raise NotImplementedError("build(): cablear el StateGraph (G1+); el run puro ya está")
