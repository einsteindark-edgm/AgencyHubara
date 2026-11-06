"""Con la conversación en manos del equipo, Ventas V2 no le habla al cliente
(tarjeta del ciclo #15).

La ruta humano solo la miraba el router del webhook: con `active_route=humano`
no arranca el workflow ni le manda la señal. Pero un turno puede entrar por
otro camino: el traspaso de remarketing (el cliente le contestó al toque y el
dispatcher despierta a Ventas), un mensaje que quedó en cola cuando la red de
seguridad escaló, o una escalación desde el dashboard con el workflow vivo. En
esos turnos el bot le respondía al cliente encima de la persona que atendía.

Ahora, antes de cada turno, el V2 lee quién tiene el chat (la misma lectura de
la caída del LLM, `read_reply_window`). Con `humano`, el turno no corre y el
workflow se apaga: los mensajes los ve el equipo en el dashboard (los guarda
el ingest, no el turno). Si la lectura falla, el turno sigue como antes: una
falla técnica no le quita el chat al bot.

Comando nuevo: el gate vive acá (el archivo del V2 no llama `workflow.patched`,
test AST). Una history sin el marcador re-juega igual; un run vivo al
desplegar sigue sin esto hasta que termine (`patched` se memoiza por run).
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.sales.activities import read_reply_window_activity

HUMAN_ROUTE_PATCH = "human-route-silence-v1"

_ROUTE_HUMANO = "humano"


async def team_owns_chat(session_id: str) -> bool:
    """True si la conversación es del equipo (`active_route == humano`)."""
    if not workflow.patched(HUMAN_ROUTE_PATCH):
        return False
    try:
        window = await workflow.execute_activity(
            read_reply_window_activity,
            session_id,
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
    except ActivityError:
        workflow.logger.warning(f"read_reply_window falló para {session_id}: el turno sigue como antes")
        return False
    return window.route == _ROUTE_HUMANO
