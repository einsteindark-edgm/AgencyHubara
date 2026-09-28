"""Adapter AgentSpan de `entity-economics`: `@tool`. El import del runtime vive DENTRO
de la función para que el módulo sea importable sin agentspan."""
from __future__ import annotations

from tools.entity_economics.impl import run


def as_agentspan_tool():
    from agentspan.agents import tool  # type: ignore

    @tool
    def entity_economics(payload: dict) -> dict:
        """Métricas de UNA entidad (campaña/segmento/anuncio/tarjeta) desde totales crudos: retorno, costo por venta, costo por chat, chat→venta, frecuencia, CTR, ticket; denominador 0 -> null, nunca un número inventado."""
        return run(payload=payload)

    return entity_economics
