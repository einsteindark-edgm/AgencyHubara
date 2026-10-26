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
    tools_used: list[str] | None = None,
    delivered: list[dict[str, str]] | None = None,
    agent: str | None = None,
) -> None:
    """Append ``role=assistant`` event al JSONL de la sesion.

    El timestamp lo agrega el store (ISO UTC). ``tools_used`` (opcional, param
    posicional 3 — los callers viejos con 2 args siguen funcionando): nombres
    de las tools ejecutadas en el turno; evidencia para el juez del eval (ver
    docstring de ``append_assistant_event``). NO confundir con ``tool_calls``
    (payload completo), que sigue fuera del contrato v1.

    ``delivered`` (param posicional 4, opcional): lo que devolvió
    ``send_whatsapp_message_activity`` — ``[{wamid, text}]`` de cada burbuja
    que Meta aceptó. El evento guarda esos ``wamids``: el chat les une el
    estado que informe WhatsApp (doble check, caso 2026-10-15).

    ``agent`` (param posicional 5, opcional): el agente que lo escribió si no
    es ventas (``"remarketing"``); queda en el evento (caso 2026-10-16). Solo
    payload: los callers viejos con 2–4 args siguen funcionando.
    """
    if not content:
        # Empty/None content -> el agente decidio no responder (ej. tool-only
        # turn). Nada que persistir como "mensaje" visible para el operador.
        return

    store = FilesystemMessageHistoryStore(WORKSPACE_VAULT_DIR)
    wamids = [b.get("wamid") or "" for b in delivered or () if isinstance(b, dict)]
    store.append_assistant_event(session_id, content, tools_used=tools_used, wamids=wamids, agent=agent)
