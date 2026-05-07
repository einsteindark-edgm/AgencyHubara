"""Prompts puros (sin side effects, sin I/O) del dominio Sales.

Estos textos son business logic: codifican la decision de como hablarle al
LLM al detectar un evento del workflow (ej: ghosting). Viven aqui (no en el
workflow) para que sean testeables sin Temporal y para que el cambio de un
prompt sea un PR plano sin tocar el grafo de tasks.

PR-E: este archivo se movio de ``domain/policies/prompts.py`` al top-level.
A 1.3K LoC el sub-folder hexagonal ``domain/policies/`` no aporta — un solo
modulo plano con la utilidad pura es mas claro de descubrir y testear.
"""
from __future__ import annotations

_GHOSTING_PROMPT = (
    "[SISTEMA]: El usuario no ha respondido nada nuevo durante bastante tiempo "
    "(Ghosting). Evalúa la conversación completa rápidamente. Tu tarea ES "
    "OBLIGATORIAMENTE usar la herramienta manage_conversation_tag marcándolo como "
    "INTERESADO si vimos alguna intención, o RECHAZO si era Spam o desinterés "
    "total. REGLA DE ORO: NO generes ninguna respuesta en crudo ni le dirijas la "
    "palabra textualmente al usuario, debes SOLO llamar a la herramienta en "
    "silencio y luego termina tus labores."
)


def build_ghosting_prompt() -> str:
    """Trigger inyectado al LLM cuando el usuario lleva mucho rato sin responder."""
    return _GHOSTING_PROMPT
