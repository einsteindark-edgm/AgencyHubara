"""Capability `roas-cac` (analyzer). Usa la tool de catálogo `recommend-budget`
INYECTADA por el binding del manifest (`uses: recommend-budget@1`) — demuestra el
reuso de una tool agnóstica en runtime. Esqueleto PURO (G-DET).

- `run(input, *, ports, tools)` — PURA; `tools["recommend-budget"]` la inyecta el loader.
- `build()`                     — StateGraph LangGraph (G1+).
"""
from __future__ import annotations


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    tools = tools or {}
    recommend = tools["recommend-budget"]  # inyectada del catálogo por el binding
    result = recommend(adsets=input["adsets"], total_budget=input["total_budget"])
    return {"allocations": result["allocations"], "analyzed_adsets": len(input["adsets"])}


def build():
    try:
        from langgraph.graph import StateGraph  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e
    raise NotImplementedError("build(): cablear el StateGraph (G1+); el run puro ya está")
