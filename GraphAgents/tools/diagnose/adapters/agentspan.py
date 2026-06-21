"""Adapter AgentSpan de `diagnose`: `@tool`. El import del runtime vive DENTRO
de la función para que el módulo sea importable sin agentspan."""
from __future__ import annotations

from tools.diagnose.impl import run


def as_agentspan_tool():
    from agentspan.agents import tool  # type: ignore

    @tool
    def diagnose(payload: dict) -> dict:
        """Tabla de umbrales determinista (MER<2.0, drop-off>40%) -> recomendacion (scale/rotate/review/insufficient)."""
        return run(payload=payload)

    return diagnose
