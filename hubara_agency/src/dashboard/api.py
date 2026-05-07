from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import asyncio
import json
import os
from src.platform.config import WORKSPACE_VAULT_DIR

router = APIRouter()

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
            
            if metadata_file.exists():
                try:
                    data = json.loads(metadata_file.read_text(encoding="utf-8"))
                    tag = data.get("tag", tag)
                    motivo = data.get("motivo", motivo)
                    active_route = data.get("active_route", active_route)
                    phone_number_id = data.get("phone_number_id")
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
                
                # Clasificador de eventos para facilitar el frontend
                role = msg_obj.get("role")
                if role == "user":
                    msg_obj["ui_type"] = "user_message"
                elif role == "tool":
                    msg_obj["ui_type"] = "tool_execution_result"
                elif role == "assistant":
                    if msg_obj.get("tool_calls"):
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
    
    metadata_file = session_path / "metadata.json"
    if metadata_file.exists():
        try:
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            tag = data.get("tag", tag)
            motivo = data.get("motivo", motivo)
            active_route = data.get("active_route", active_route)
            phone_number_id = data.get("phone_number_id")
            status_history = data.get("status_history", [])
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
        "status_history": status_history,
        "messages": messages
    }
