"""Adapter AgentSpan de `parse-conversations`: `@tool`. El import del runtime vive DENTRO
de la función para que el módulo sea importable sin agentspan."""
from __future__ import annotations

from tools.parse_conversations.impl import run


def as_agentspan_tool():
    from agentspan.agents import tool  # type: ignore

    @tool
    def parse_conversations(payload: dict) -> dict:
        """TODO: describí qué hace parse-conversations"""
        return run(payload=payload)

    return parse_conversations
