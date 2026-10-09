"""El aviso del cierre por abandono del bot nuevo (V2) — premortem 2026-10-09.

`decide_ghosting_action(sesión)` espera la capacidad `cierre` (Jev, hasta
10,25 s) y el V2 le daba 10 s con 2 intentos y sin red: con Jev lento los dos
intentos vencían, el workflow fallaba y nadie etiquetaba la conversación ni
perseguía los datos de envío del cliente. Ahora tiene 30 s y, si aun así
falla, sale el aviso de la regla (`decide_ghosting_action("")`, sin I/O).

Replay: el camino nuevo solo corre donde antes el workflow moría (una
historia así terminó fallida y no se re-juega): no necesita `patched`. Lo
usa SOLO `HubaraSalesSessionWorkflowV2`; el V1 (`sales_session.py`) queda
congelado.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.sales.activities import decide_ghosting_action

#: Jev espera hasta 10 s (más su reintento de falla pasajera): el aviso no se
#: vence antes que Jev.
_DECIDED_TIMEOUT = timedelta(seconds=30)


async def ghost_trigger(session_id: str) -> str:
    """El aviso que se le inyecta al LLM cuando el cliente dejó de responder."""
    try:
        return await workflow.execute_activity(
            decide_ghosting_action,
            session_id,
            start_to_close_timeout=_DECIDED_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
    except ActivityError:
        workflow.logger.error(
            f"decide_ghosting_action falló para {session_id}: sale el aviso de la regla (sin Jev)"
        )
        return await workflow.execute_activity(
            decide_ghosting_action,
            "",
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
