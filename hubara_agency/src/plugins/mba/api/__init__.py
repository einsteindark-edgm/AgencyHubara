"""API del plugin ``mba`` (plano de gestión, protegido por auth del shell).

Rutas montadas bajo ``/api/mba``: lista de agentes MBA, la configuración
exacta que se enviaría a Meta por agente, quién controla el hilo de una
sesión (D1.5: ``control_owner`` que chats persiste desde el webhook
``messaging_handovers``; se lee por canal 1, el metadata store del SDK) y
devolverle el hilo a Meta Business Agent (D1.6: ``ReleaseThread`` con la
política de release; guardas fail-closed → 503/403/404). Las tools del
connector (públicas, con API key propia) viven en ``connector.py``.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.plugins.mba.adapters.thread_control import MetaThreadControl
from src.plugins.mba.domain.release_policy import ReleaseTrigger
from src.plugins.mba.service import list_agents, load_agent
from src.plugins.mba.use_cases.release_thread import ReleaseThread
from src.sdk.runtime import (
    WORKSPACE_VAULT_DIR,
    FilesystemMetadataStore,
    mba_customer_allowed,
    mba_standby_enabled,
)

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
    # ``fullmatch``: ``$`` aceptaría un ``\n`` final.
    if not _SESSION_KEY_RE.fullmatch(session_key):
        raise HTTPException(status_code=422, detail="session_key debe ser wa_<dígitos>")
    # El layout del vault es del store (SDK), no de este plugin: una sesión sin
    # metadata (inexistente, vacío o corrupto) lee como ``{}``.
    data = FilesystemMetadataStore(WORKSPACE_VAULT_DIR).read(session_key)
    if not data:
        raise HTTPException(status_code=404, detail=f"sesión desconocida: {session_key}")
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



class ReleaseBody(BaseModel):
    trigger: ReleaseTrigger = ReleaseTrigger.MANUAL
    metadata: str | None = Field(default=None, max_length=2000)  # tope de Meta
    order_registered: bool = False
    agent_event_emitted: bool = False


def get_release_thread() -> ReleaseThread:
    return ReleaseThread(
        metadata_store=FilesystemMetadataStore(WORKSPACE_VAULT_DIR),
        port=MetaThreadControl(),
        is_customer_allowed=mba_customer_allowed,
        is_enabled=mba_standby_enabled,
        phone_number_id_fallback=lambda: os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""),
    )


_RELEASE_GUARD_STATUS = {"mba_disabled": 503, "customer_not_enabled": 403, "session_unknown": 404}


@router.post("/sessions/{session_key}/control/release")
async def release_session_control(
    session_key: str,
    body: ReleaseBody | None = None,
    use_case: ReleaseThread = Depends(get_release_thread),
) -> dict[str, Any]:
    """Devolver el hilo a Meta Business Agent según la política de release
    (§D1.6). ``released=false`` con ``reason`` cuando la política dice que no
    o Meta rechazó; las guardas (flag, lista cerrada, sesión) son 503/403/404."""
    if not _SESSION_KEY_RE.fullmatch(session_key):
        raise HTTPException(status_code=422, detail="session_key debe ser wa_<dígitos>")
    body = body or ReleaseBody()
    outcome = await use_case.execute(
        session_key,
        body.trigger,
        order_registered=body.order_registered,
        agent_event_emitted=body.agent_event_emitted,
        metadata=body.metadata,
    )
    status = _RELEASE_GUARD_STATUS.get(outcome.reason)
    if status is not None:
        raise HTTPException(status_code=status, detail=outcome.reason)
    return asdict(outcome)
