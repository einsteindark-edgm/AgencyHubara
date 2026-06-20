"""Adapter AgentSpan de `blended-unit-economics`: `@tool`. El import del runtime vive DENTRO
de la función para que el módulo sea importable sin agentspan."""
from __future__ import annotations

from tools.blended_unit_economics.impl import run


def as_agentspan_tool():
    from agentspan.agents import tool  # type: ignore

    @tool
    def blended_unit_economics(payload: dict) -> dict:
        """Las 5 metricas CTWA puras (drop-off, costo/conversacion, MER, CPA global, win-rate) desde totales crudos; denominador 0 -> null, nunca un numero inventado."""
        return run(payload=payload)

    return blended_unit_economics
