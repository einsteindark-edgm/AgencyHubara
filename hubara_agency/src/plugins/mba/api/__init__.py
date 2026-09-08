"""API del plugin ``mba`` (plano de gestión, protegido por auth del shell).

Rutas montadas bajo ``/api/mba``: lista de agentes MBA, la configuración
exacta que se enviaría a Meta por agente y quién controla el hilo de una
sesión (D1.5: ``control_owner`` que chats persiste desde el webhook
``messaging_handovers``; se lee por canal 1, el metadata store del SDK). Las
tools del connector (públicas, con API key propia) viven en ``connector.py``.
"""
from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from src.plugins.mba.service import list_agents, load_agent
from src.sdk.runtime import WORKSPACE_VAULT_DIR, FilesystemMetadataStore

router = APIRouter()

#: Últimos eventos de control que devuelve el endpoint (el vault guarda 50).
CONTROL_HISTORY_LIMIT = 20
#: SEC-12: la clave se vuelve un path del vault — solo sesiones ``wa_<dígitos>``.
_SESSION_KEY_RE = re.compile(r"^wa_\d{6,15}$")


@router.get("/agents")
async def get_agents() -> dict[str, Any]:
    return {"agents": [asdict(a) for a in list_agents()]}


@router.get("/agents/{agent_id}/config")
async def get_agent_config(agent_id: str) -> dict[str, Any]:
    cfg = load_agent(agent_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"agente MBA desconocido: {agent_id}")
    return asdict(cfg)


@router.get("/sessions/{session_key}/control")
async def get_session_control(session_key: str) -> dict[str, Any]:
    """Quién responde al cliente hoy (``mba`` | ``hubara`` | ``null`` si Meta
    nunca avisó) y los últimos cambios de control."""
    if not _SESSION_KEY_RE.match(session_key):
        raise HTTPException(status_code=422, detail="session_key debe ser wa_<dígitos>")
    if not (WORKSPACE_VAULT_DIR / session_key / "metadata.json").is_file():
        raise HTTPException(status_code=404, detail=f"sesión desconocida: {session_key}")
    data = FilesystemMetadataStore(WORKSPACE_VAULT_DIR).read(session_key)
    history = data.get("control_history")
    history = [h for h in history if isinstance(h, dict)] if isinstance(history, list) else []
    return {
        "session_key": session_key,
        "control_owner": data.get("control_owner"),
        "control_owner_since_ms": data.get("control_owner_since_ms"),
        "control_owner_updated_at_ms": data.get("control_owner_updated_at_ms"),
        "control_owner_app_id": data.get("control_owner_app_id"),
        "history": history[-CONTROL_HISTORY_LIMIT:],
    }
