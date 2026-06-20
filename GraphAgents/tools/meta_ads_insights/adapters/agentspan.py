"""Adapter AgentSpan de `meta-ads-insights`: `@tool`. El import del runtime vive DENTRO
de la función para que el módulo sea importable sin agentspan."""
from __future__ import annotations

from tools.meta_ads_insights.impl import run


def as_agentspan_tool():
    from agentspan.agents import tool  # type: ignore

    @tool
    def meta_ads_insights(payload: dict) -> dict:
        """Parsea la respuesta de Graph /insights (con actions) a filas CTWA normalizadas; recibe el JSON, no llama a Meta."""
        return run(payload=payload)

    return meta_ads_insights
