"""Activity wrapper para inyectar contexto dinámico de turno desde un workflow.

Mantiene determinismo del workflow (R-DET): el cómputo de `datetime.now()`
ocurre dentro de la activity, no en el workflow. El resultado es JSON-safe
(`str`, R-JSON) para viajar limpio por el history de Temporal.

Cuándo usarla:
  - Desde el workflow `HubaraSalesSessionWorkflow` si se requiere inyectar
    la hora justo antes de un `_run_turn` (ej: trigger de ghosting,
    re-saludo en handoff). El path normal (mensaje del cliente entrante)
    NO necesita esta activity, ya que `load_or_start_sales_session.py`
    llama al helper puro `context.build_bogota_context_string()` desde
    código sincrónico (use case, fuera del workflow) antes de signalar.

Si se invoca desde el workflow, registrarla en el worker junto al resto
de activities de Sales.
"""
from __future__ import annotations

from temporalio import activity

from src.plugins.chats.agent.sales.context import build_bogota_context_string


@activity.defn(name="compute_bogota_context")
async def compute_bogota_context_activity() -> str:
    """Devuelve el bloque de contexto dinámico (hora + saludo de Colombia).

    R-DET safe: I/O de `datetime.now(TZ)` ocurre acá (activity), no en el
    workflow. R-JSON safe: retorno `str` plano.
    """
    return build_bogota_context_string()
