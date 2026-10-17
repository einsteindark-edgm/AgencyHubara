"""Lo que el cliente le escribió a remarketing, para el turno de Ventas V2 que
sigue al traspaso (caso 2026-10-16).

Remarketing le pasó el chat a Ventas con un resumen («Cliente respondió "Si"
al gancho…»). Ventas arrancó sin el «Si», sin lo que había dicho un colega y
sin el gancho: su historial terminaba en un «entonces no» del cliente, y el
encuadre del traspaso le ofrecía `NO_MESSAGE` «si no hay mensaje del cliente».
Contestó eso y un colega tuvo que mandar el formulario a mano.

Ahora, al leer un traspaso, el V2 lee también lo que escribió el cliente
(`read_handoff_reply`): si escribió, su mensaje entra al turno tal cual, con la
nota de lo que Ventas no vio, y el resumen queda como contexto.

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
    from src.plugins.chats.agent.sales.activities import read_handoff_reply_activity
    from src.plugins.chats.agent.sales.contracts import HandoffReply
    from src.sdk.agentkit import PendingMessage

HANDOFF_REPLY_PATCH = "handoff-customer-reply-v1"


async def handoff_reply(session_id: str, already_pending: list[str]) -> HandoffReply | None:
    """Lo que el cliente escribió después de lo último del chat, o None. Si la
    lectura falla, el turno sigue como antes (solo con el resumen)."""
    if not workflow.patched(HANDOFF_REPLY_PATCH):
        return None
    try:
        return await workflow.execute_activity(
            read_handoff_reply_activity,
            args=[session_id, already_pending],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
    except ActivityError:
        workflow.logger.warning(
            f"read_handoff_reply falló para {session_id}: el traspaso sigue solo con el resumen"
        )
        return None


def reply_message(reply: HandoffReply) -> PendingMessage:
    """El mensaje del turno: lo que escribió el cliente, como si acabara de
    llegar (con su wamid para la traza y el clasificador)."""
    return PendingMessage(
        message=reply.text,
        inbound_meta={
            "wamid": reply.wamids[-1] if reply.wamids else None,
            "ts_ms": reply.ts_ms,
            "kind": "text",
            "text": reply.customer_text,
        },
    )
