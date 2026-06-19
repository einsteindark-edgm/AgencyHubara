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
    """`StateGraph` LangGraph (adapter al runtime real). El único nodo reusa el
    `run()` puro — la lógica vive UNA sola vez (G-DET). Sin LLM → AgentSpan lo
    corre por el path passthrough como una task durable. `compile(name=...)` es lo
    que AgentSpan lee como nombre del agente."""
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    from tools.hello.impl import run as hello

    class State(TypedDict, total=False):
        name: str
        greeting: str

    def greet(state: State) -> dict:
        return run(dict(state), tools={"hello": hello})

    g = StateGraph(State)
    g.add_node("greet", greet)
    g.add_edge(START, "greet")
    g.add_edge("greet", END)
    return g.compile(name="greeter")
