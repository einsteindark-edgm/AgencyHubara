"""Adapter AgentSpan de `complement-funnel`: `@tool`. El import del runtime vive DENTRO
de la función para que el módulo sea importable sin agentspan."""
from __future__ import annotations

from tools.complement_funnel.impl import run


def as_agentspan_tool():
    from agentspan.agents import tool  # type: ignore

    @tool
    def complement_funnel(payload: dict) -> dict:
        """Complementa las campanas del MCP entities con la conversacion de Graph /insights actions (por campaign_id) — recupera la conversacion que el entities da en 0 (purchase-conversion)."""
        return run(payload=payload)

    return complement_funnel
