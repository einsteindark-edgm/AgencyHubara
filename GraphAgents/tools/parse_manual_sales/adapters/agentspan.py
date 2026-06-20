"""Adapter AgentSpan de `parse-manual-sales`: `@tool`. El import del runtime vive DENTRO
de la función para que el módulo sea importable sin agentspan."""
from __future__ import annotations

from tools.parse_manual_sales.impl import run


def as_agentspan_tool():
    from agentspan.agents import tool  # type: ignore

    @tool
    def parse_manual_sales(payload: dict) -> dict:
        """Normaliza filas de ventas manuales de WhatsApp; valida y mapea el alias total_revenue -> total_revenue_cop."""
        return run(payload=payload)

    return parse_manual_sales
