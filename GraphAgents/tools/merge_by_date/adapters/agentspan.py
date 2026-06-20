"""Adapter AgentSpan de `merge-by-date`: `@tool`. El import del runtime vive DENTRO
de la función para que el módulo sea importable sin agentspan."""
from __future__ import annotations

from tools.merge_by_date.impl import run


def as_agentspan_tool():
    from agentspan.agents import tool  # type: ignore

    @tool
    def merge_by_date(payload: dict) -> dict:
        """Inner-join de dos feeds diarios (insights + ventas) por fecha; las fechas sin match se SURFACEAN (no se dropean en silencio); fecha duplicada = error."""
        return run(payload=payload)

    return merge_by_date
