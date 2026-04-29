"""Prompts puros del dominio Remarketing."""
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
