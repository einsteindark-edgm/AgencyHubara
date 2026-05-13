"""Temporal activities cross-domain para el log de mensajes de sesion.

Llamada por:
  * ``HubaraSalesSessionWorkflow`` (sales_whatsapp/workflows/sales_session.py)
  * ``HubaraRemarketingWorkflow`` (remarketing_whatsapp/workflows/remarketing.py)

Se invoca DESPUES del ``send_whatsapp_message_activity``: si el send falla y
reintenta, no contamina el log con mensajes que el cliente nunca vio. En
contrapartida, si el send funciona pero esta activity falla y reintenta, el
operador puede ver el mismo mensaje duplicado — costo aceptable para v1 (el
contrato append-only no garantiza idempotencia, eso es scope mayor).

Vive en ``platform`` porque la usan ambos workflows agentes (R-DIP #10).
"""
from __future__ import annotations

from temporalio import activity

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.session_history.store import FilesystemMessageHistoryStore


@activity.defn(name="persist_assistant_message_activity")
async def persist_assistant_message_activity(
    session_id: str,
    content: str,
) -> None:
    """Append ``role=assistant`` event al JSONL de la sesion.

    El timestamp lo agrega el store (ISO UTC). ``tool_calls`` quedan fuera del
    contrato de esta activity v1 — si en el futuro se quieren persistir, se
    extiende la signature con un parametro opcional o se crea una segunda
    activity (``persist_agent_tool_call_activity``).
    """
    if not content:
        # Empty/None content -> el agente decidio no responder (ej. tool-only
        # turn). Nada que persistir como "mensaje" visible para el operador.
        return

    store = FilesystemMessageHistoryStore(WORKSPACE_VAULT_DIR)
    store.append_assistant_event(session_id, content)
