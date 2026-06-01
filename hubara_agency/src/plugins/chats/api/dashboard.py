from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import asyncio
import json
import os
from src.platform.config import WORKSPACE_VAULT_DIR

router = APIRouter()


# Reason por la que el agente escala una venta para que un humano verifique el
# pago (lo escribe `escalate_to_human(reason_category="PAYMENT_VERIFICATION_PENDING")`).
_PAYMENT_PENDING_REASON = "PAYMENT_VERIFICATION_PENDING"


def _compute_pending_payment_order_id(data: dict) -> str | None:
    """¿Esta sesión tiene un pedido esperando que un humano confirme el pago?

    Devuelve el `order_id` (id backend de Medusa) a confirmar, o ``None``.

    El agente, al cerrar una venta, registra el pedido en Medusa y escala con
    `escalate_to_human(reason_category="PAYMENT_VERIFICATION_PENDING")` — eso
    deja en el `metadata.json` del chat: ``active_route="humano"``,
    ``escalation_reason="PAYMENT_VERIFICATION_PENDING"`` y el
    ``registered_order`` exitoso. Esta función reconoce ese estado para que el
    frontend muestre el botón "Confirmar pago" en el chat (mismo endpoint que
    el tablero de orders).

    Cuando el humano confirma el pago (desde el chat o desde orders), el command
    `confirm_payment` reescribe este mismo `metadata.json`: ``tag`` pasa a
    ``COMPRA_EXITOSA`` y el episodio del pedido recibe ``payment_confirmed_at_ms``.
    Cualquiera de esas marcas hace que esta función devuelva ``None`` → el botón
    desaparece solo en el próximo tick del SSE. Idéntico para ``RECHAZO`` (cancel).
    """
    if data.get("active_route") != "humano":
        return None
    if data.get("escalation_reason") != _PAYMENT_PENDING_REASON:
        return None
    # tag terminal → ya resuelto (confirmado o rechazado).
    if data.get("tag") in ("COMPRA_EXITOSA", "RECHAZO"):
        return None
    registered = data.get("registered_order")
    if not isinstance(registered, dict) or registered.get("success") is not True:
        return None
    order_id = registered.get("order_id")
    if not isinstance(order_id, str) or not order_id:
        return None
    # Doble chequeo: si el episodio de este pedido ya tiene la marca de pago
    # confirmado (la escribe `apply_payment_confirmation_to_chat_metadata`), no
    # está pendiente — aunque el tag no se haya actualizado por algún flujo raro.
    for episode in data.get("episodes") or []:
        if (
            isinstance(episode, dict)
            and episode.get("order_id") == order_id
            and episode.get("payment_confirmed_at_ms")
        ):
            return None
    return order_id


# Note: the liveness probe for the whole FastAPI app is `GET /` (defined in
# src/main.py:24). The frontend pipeline polls that endpoint before invoking
# Playwright. We intentionally do NOT add a duplicate `/api/dashboard/health`
# here — two probes mean two truths to keep in sync.


async def dashboard_event_generator():
    """Generador asíncrono para enviar eventos con retraso de 2.5s"""
    while True:
        data = await list_dashboard_sessions()
        yield f"data: {json.dumps(data)}\n\n"
        await asyncio.sleep(2.5)

@router.get("/stream")
async def stream_dashboard_sessions():
    """Server-Sent Events endpoint for realtime dashboard updates."""
    return StreamingResponse(dashboard_event_generator(), media_type="text/event-stream")

@router.get("/sessions")
async def list_dashboard_sessions():
    """
    Returns a realtime list of all active or past WhatsApp sessions, 
    complete with their assigned tags, route handlers, and metadata.
    """
    if not WORKSPACE_VAULT_DIR.exists():
        # Vault doesn't exist yet, return empty
        return {"sessions": []}
        
    sessions = []
    
    for entry in os.listdir(WORKSPACE_VAULT_DIR):
        session_path = WORKSPACE_VAULT_DIR / entry
        if session_path.is_dir() and entry.startswith("wa_"):
            metadata_file = session_path / "metadata.json"
            tag = "NO_ETIQUETADO"
            motivo = "Sin diagnóstico todavía"
            active_route = "ventas"
            phone_number_id = None
            pending_payment_order_id = None

            if metadata_file.exists():
                try:
                    data = json.loads(metadata_file.read_text(encoding="utf-8"))
                    tag = data.get("tag", tag)
                    motivo = data.get("motivo", motivo)
                    active_route = data.get("active_route", active_route)
                    phone_number_id = data.get("phone_number_id")
                    pending_payment_order_id = _compute_pending_payment_order_id(data)
                except json.JSONDecodeError:
                    pass
            
            # Buscamos el timestamp de la ultima conversacion
            last_updated = 0
            history_file = session_path / "sessions" / f"{entry}.jsonl"
            if history_file.exists():
                last_updated = history_file.stat().st_mtime
            else:
                last_updated = session_path.stat().st_mtime
            
            sessions.append({
                "session_id": entry,
                "phone_number": entry.replace("wa_", ""),
                "tag": tag,
                "motivo": motivo,
                "active_agent_route": active_route,
                "phone_number_id": phone_number_id,
                "pending_payment_order_id": pending_payment_order_id,
                "last_updated_timestamp": last_updated
            })
            
    # Sort from most recent to oldest
    sessions.sort(key=lambda x: x["last_updated_timestamp"], reverse=True)
    return {"sessions": sessions}
    

@router.get("/sessions/{session_id}")
async def get_session_history(session_id: str):
    """
    Returns the raw historical chat events from exoclaw-temporal JSONL.
    """
    session_path = WORKSPACE_VAULT_DIR / session_id
    if not session_path.exists() or not session_path.is_dir():
        raise HTTPException(status_code=404, detail="Session not found in Vault")
        
    history_file = session_path / "sessions" / f"{session_id}.jsonl"
    if not history_file.exists():
        return {"session_id": session_id, "messages": []}
        
    messages = []
    
    with open(history_file, 'r', encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg_obj = json.loads(line)
                
                # Clasificador de eventos para facilitar el frontend.
                # `sender=="human"` (mensajes del operador humano via dashboard
                # handoff) se proyecta como `human_message` para que el bubble
                # se renderice distinto y se diferencie del agente.
                role = msg_obj.get("role")
                if role == "user":
                    msg_obj["ui_type"] = "user_message"
                elif role == "tool":
                    msg_obj["ui_type"] = "tool_execution_result"
                elif role == "assistant":
                    if msg_obj.get("sender") == "human":
                        msg_obj["ui_type"] = "human_message"
                    elif msg_obj.get("tool_calls"):
                        msg_obj["ui_type"] = "agent_tool_call"
                    else:
                        msg_obj["ui_type"] = "agent_message"
                else:
                    msg_obj["ui_type"] = "system_event"
                
                messages.append(msg_obj)
            except json.JSONDecodeError:
                continue
                
    tag = "NO_ETIQUETADO"
    motivo = "Sin diagnóstico todavía"
    active_route = "ventas"
    phone_number_id = None
    status_history = []
    pending_payment_order_id = None

    metadata_file = session_path / "metadata.json"
    if metadata_file.exists():
        try:
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            tag = data.get("tag", tag)
            motivo = data.get("motivo", motivo)
            active_route = data.get("active_route", active_route)
            phone_number_id = data.get("phone_number_id")
            status_history = data.get("status_history", [])
            pending_payment_order_id = _compute_pending_payment_order_id(data)
        except json.JSONDecodeError:
            pass

    memory_content = None
    memory_file = session_path / "memory" / "MEMORY.md"
    if memory_file.exists():
        memory_content = memory_file.read_text(encoding="utf-8")

    return {
        "session_id": session_id,
        "phone_number": session_id.replace("wa_", ""),
        "tag": tag,
        "motivo": motivo,
        "memory_content": memory_content,
        "active_agent_route": active_route,
        "phone_number_id": phone_number_id,
        "pending_payment_order_id": pending_payment_order_id,
        "status_history": status_history,
        "messages": messages
    }
