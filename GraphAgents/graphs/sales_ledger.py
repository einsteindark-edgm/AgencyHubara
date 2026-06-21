"""Capability `sales-ledger` (extractor). PURA: toma el JSON de ventas manuales de
WhatsApp y lo normaliza con la tool `parse-manual-sales`. El agente recibe el JSON
(no lee archivos ni toca IO).

- run(input, *, ports, tools) — PURA (G-RUN-SIG).
- build()                     — StateGraph LangGraph (G1+).
"""
from __future__ import annotations


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    if "payload" not in input:  # MF-7: el seam falla con error de DOMINIO, no KeyError
        raise ValueError(
            "sales-ledger: falta 'payload' (el JSON de ventas que deposita el central/operador)"
        )
    tools = tools or {}
    parse = tools["parse-manual-sales"]  # tool de catálogo inyectada por el binding
    return {"sales": parse(payload=input["payload"])["sales"]}


def build():
    """`StateGraph` LangGraph (G1) — single-node que REUSA el `run()` puro (la lógica vive
    UNA vez, G-DET). AgentSpan lo corre por passthrough (single-node = una task; multi-nodo
    sería por-nodo, L-14). `compile(name=...)` = el nombre que lee AgentSpan."""
    try:
        from typing import TypedDict

        from langgraph.graph import END, START, StateGraph
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e

    from tools.parse_manual_sales.impl import run as parse

    class State(TypedDict, total=False):
        payload: object   # el JSON de ventas manuales (lo deposita el central/operador)
        sales: list       # ← run() (normalizado)

    def extract(state: State) -> dict:
        return run(dict(state), tools={"parse-manual-sales": parse})

    g = StateGraph(State)
    g.add_node("extract", extract)
    g.add_edge(START, "extract")
    g.add_edge("extract", END)
    return g.compile(name="sales-ledger")
