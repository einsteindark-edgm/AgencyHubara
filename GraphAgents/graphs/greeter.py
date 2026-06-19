"""Capability `greeter` (reporter) — el hola mundo. Usa la tool de catálogo
`hello` INYECTADA por el binding del manifest. Esqueleto PURO (G-DET).

- `run(input, *, ports, tools)` — pura; `tools["hello"]` la inyecta el loader.
- `build()`                     — StateGraph LangGraph (G1+).
"""
from __future__ import annotations


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    tools = tools or {}
    hello = tools["hello"]  # inyectada del catálogo por `uses: hello@1`
    return hello(name=input.get("name", "mundo"))


def build():
    try:
        from langgraph.graph import StateGraph  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e
    raise NotImplementedError("build(): cablear el StateGraph (G1+); el run puro ya está")
