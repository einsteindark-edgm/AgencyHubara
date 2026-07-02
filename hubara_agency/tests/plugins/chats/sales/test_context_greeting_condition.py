"""El bloque bogota-context condiciona el saludo al HISTORIAL, no al workflow.

Bug run 019f24bf (cliente real): el cliente venía comprando y 4 minutos
después el bot abrió con "Buenas tardes 🤍" de nuevo. Causa: el bloque decía
'Saludo apropiado para esta franja si abres una sesión nueva' y el LLM leyó
"workflow nuevo = sesión nueva" — pero para el CLIENTE la conversación de
WhatsApp es una sola, continua. La condición correcta es sobre el historial
visible: saluda solo si es el primer contacto; si ya hay CUALQUIER intercambio
previo (incluso un mensaje proactivo del bot), retoma sin re-saludar.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.plugins.chats.agent.sales.context import build_bogota_context_string

_BOGOTA = ZoneInfo("America/Bogota")


def test_greeting_conditioned_on_first_contact_not_new_session() -> None:
    s = build_bogota_context_string(
        now=datetime(2026, 7, 2, 16, 33, tzinfo=_BOGOTA)
    )
    # El saludo de franja sigue presente (contrato con los tests existentes).
    assert "Buenas tardes" in s
    # La condición ya NO es "sesión nueva" (ambigua: el LLM la leía como
    # "workflow nuevo" y re-saludaba a los 4 minutos — run 019f24bf).
    assert "sesión nueva" not in s
    # Es explícita sobre el historial: primer mensaje ⇒ saluda; si ya hay
    # conversación previa ⇒ retoma sin volver a saludar.
    lowered = s.lower()
    assert "primer mensaje" in lowered or "primer contacto" in lowered
    assert "sin volver a saludar" in lowered or "no vuelvas a saludar" in lowered
