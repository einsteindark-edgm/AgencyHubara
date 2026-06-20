"""Capability `ctwa-insights` (extractor). Esqueleto PURO (G-DET): toma el JSON crudo
de Graph /insights (lo deposita el central), lo parsea con la tool `meta-ads-insights`
y lo COLAPSA a nivel cuenta por día (suma las campañas; renombra a conversations_started).
El agente NO llama a Meta — recibe el JSON.

- run(input, *, ports, tools) — PURA, para el LocalRuntime / golden-replay (G-RUN-SIG).
- build()                     — el StateGraph LangGraph (G1+).
"""
from __future__ import annotations


def _collapse_to_daily(rows: list) -> list:
    """Suma las campañas en una fila de cuenta por fecha; renombra a conversations_started."""
    agg: dict = {}
    for r in rows:
        d = r["date"]
        a = agg.setdefault(d, [0, 0, 0])
        a[0] += r["spend_cop"]
        a[1] += r["inline_link_clicks"]
        a[2] += r["messaging_conversations_started"]
    return [
        {"date": d, "spend_cop": s, "inline_link_clicks": c, "conversations_started": conv}
        for d, (s, c, conv) in sorted(agg.items())
    ]


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    if "payload" not in input:  # MF-7: el seam central→agente falla con error de DOMINIO, no KeyError
        raise ValueError(
            "ctwa-insights: falta 'payload' (el JSON de Graph /insights que deposita el central)"
        )
    tools = tools or {}
    parse = tools["meta-ads-insights"]  # tool de catálogo inyectada por el binding (G-BIND)
    parsed = parse(payload=input["payload"])
    return {"currency": parsed["currency"], "insights": _collapse_to_daily(parsed["insights"])}


def build():
    """StateGraph LangGraph (adapter al runtime real). Requiere langgraph (G1+)."""
    try:
        from langgraph.graph import StateGraph  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e
    raise NotImplementedError("build(): cablear el StateGraph (G1+); el run puro ya está")
