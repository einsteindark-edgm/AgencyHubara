"""Adapter AgentSpan: expone `recommend-budget` como `@tool`. El import del
runtime vive DENTRO de la función para que el módulo sea importable sin agentspan
(el registry/contrato no necesita el runtime instalado).
"""
from __future__ import annotations

from tools.recommend_budget.impl import run


def as_agentspan_tool():
    from agentspan.agents import tool  # type: ignore

    @tool
    def recommend_budget(adsets: list, total_budget: float) -> dict:
        """Reparte el presupuesto entre adsets proporcional a su ROAS."""
        return run(adsets=adsets, total_budget=total_budget)

    return recommend_budget
