"""La guarda de listas del bot nuevo (V2) con el selector que de verdad salió
(VAR-01, 2026-10-07; análisis docs/calidad-llm/cobertura-motor.html).

El V2 manda el texto aunque el selector se haya rechazado (diferencia 2 con el
V1: un selector rechazado no deja al cliente sin respuesta). Pero la guarda que
cambia una lista de aromas o colores por el selector seguía mirando si el
selector se INTENTÓ: con un selector rechazado no corría y la lista salía como
texto plano. Lo usa solo `HubaraSalesSessionWorkflowV2`; su archivo no llama
`workflow.patched` (test AST): el gate vive acá, como en `bursts_v2.py`.
"""
from __future__ import annotations

from collections.abc import Collection

from temporalio import workflow

ENUMERATION_GUARD_PATCH = "enumeration-guard-picker-delivered-v1"
_VARIANT_PICKER = "present_variant_picker"


def enumeration_guard_applies(tools_used: Collection[str], delivered: Collection[str]) -> bool:
    """¿Corre la guarda de listas sobre el texto final del turno?

    Sin selector intentado, sí (el turno de hoy, sin consultar el patch). Con el
    selector entregado, no: él es el mensaje. Con el selector intentado y
    rechazado, antes no corría; ahora sí. `patched()` se consulta solo en ese
    caso, que es donde la regla nueva difiere de la vieja: una history sin el
    marker re-juega igual."""
    if _VARIANT_PICKER not in tools_used:
        return True
    if _VARIANT_PICKER in delivered:
        return False
    return workflow.patched(ENUMERATION_GUARD_PATCH)
