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
    try:
        from langgraph.graph import StateGraph  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e
    raise NotImplementedError("build(): cablear el StateGraph (G1+); el run puro ya está")
