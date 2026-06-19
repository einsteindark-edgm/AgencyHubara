"""Adapter LangGraph: expone `recommend-budget` como un nodo de StateGraph
(lee del state, devuelve el patch). Un nodo de LangGraph es solo un callable
`state -> dict`, así que acá ni siquiera hace falta importar langgraph; el core
no se toca.
"""
from __future__ import annotations

from tools.recommend_budget.impl import run


def node(state: dict) -> dict:
    out = run(adsets=state["adsets"], total_budget=state["total_budget"])
    return {"allocations": out["allocations"]}
