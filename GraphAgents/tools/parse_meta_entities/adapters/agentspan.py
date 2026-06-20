"""Adapter AgentSpan de `parse-meta-entities`: `@tool`. El import del runtime vive DENTRO
de la función para que el módulo sea importable sin agentspan."""
from __future__ import annotations

from tools.parse_meta_entities.impl import run


def as_agentspan_tool():
    from agentspan.agents import tool  # type: ignore

    @tool
    def parse_meta_entities(payload: dict) -> dict:
        """Parsea ads_get_ad_entities (MCP entities, strings formateados) a filas por-campana objetivo-aware (objetivo, spend, clicks, result_type, is_messaging)."""
        return run(payload=payload)

    return parse_meta_entities
