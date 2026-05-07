"""Prompts puros (sin side effects, sin I/O) del dominio Remarketing.

Estos textos son business logic: codifican la decision de como hablarle al
LLM al arrancar el workflow proactivo de Remarketing (saludo de recuperacion
con `motivo` y `memory_context`). Viven aqui (no en el workflow) para que
sean testeables sin Temporal y para que el cambio de un prompt sea un PR
plano sin tocar el grafo de tasks.

PR-E (ADR-2026-05-06-11): este archivo se movio de
``domain/policies/prompts.py`` al top-level — mismo patron que sales_whatsapp
PR-E (ADR-2026-05-06-07). A esta escala el sub-folder hexagonal
``domain/policies/`` no aporta: un solo modulo plano con la utilidad pura es
mas claro de descubrir y testear.
"""
from __future__ import annotations


def build_remarketing_trigger(motivo: str, memory_context: str = "") -> str:
    """Saludo proactivo inicial inyectado al LLM al arrancar el workflow.

    `motivo` es el resumen del cierre anterior (registrado por la tool de tags).
    `memory_context` es el `memory.md` del PVC (puede ser vacio).
    """
    return (
        "[SISTEMA INTERNO]: El cliente abortó o pausó la venta hace un tiempo. "
        f"MOTIVO REGISTRADO DE CIERRE: '{motivo}'. MEMORIA DE EVENTOS PASADOS:"
        f"{memory_context} Tu tarea es generar inmediatamente un saludo de "
        "contacto proactivo ofreciéndole envío gratis como beneficio para revivir "
        "la venta. REDACTA EL MENSAJE COMO SI LE ESTUVIERAS HABLANDO DIRECTAMENTE "
        "A ÉL POR WHATSAPP."
    )
