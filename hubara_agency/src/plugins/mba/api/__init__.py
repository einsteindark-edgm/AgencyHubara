"""API del plugin ``mba`` (plano de gestión, protegido por auth del shell).

Rutas montadas bajo ``/api/mba``: lista de agentes MBA, la configuración
exacta que se enviaría a Meta por agente, quién controla el hilo de una
sesión (D1.5: ``control_owner`` que chats persiste desde el webhook
``messaging_handovers``; se lee por canal 1, el metadata store del SDK) y
devolverle el hilo a Meta Business Agent (D1.6: ``ReleaseThread`` con la
política de release; guardas fail-closed → 503/403/404) y contarle a MBA una
novedad del pedido por ``agent_event`` (D1.9: ``EmitAgentEvent``, lo invoca
el ETA por cast con identidad de servicio cuando MBA controla), y llevar la
configuración autorada a Meta (D2.2: ``SyncAgent`` — plan de solo lectura +
apply confirmado por fingerprint; nunca toca ``rollout`` ni ``ai_audience``)
y gobernar el rollout (D2.3: ``RolloutControl`` — allowlist de Meta acotada a
la lista cerrada de Hubara, audiencia y ``rollout.enabled`` con readiness), y
la consola ``agent_test`` (D2.4: probar skills y conocimiento contra Meta sin
facturación ni hilos reales).
Las tools del connector (públicas, con API key propia) viven en ``connector.py``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.plugins.mba.domain.config import env_value
from src.plugins.mba.adapters.agent_event import MetaAgentEvent
from src.plugins.mba.adapters.meta_admin import (
    MbaAdminError,
    MbaAdminPort,
    MetaMbaAdmin,
)
from src.plugins.mba.adapters.sync_state import SyncStateStore
from src.plugins.mba.adapters.thread_control import MetaThreadControl
from src.plugins.mba.domain.agent_events import AGENT_EVENT_TYPES, DESCRIPTION_MAX
from src.plugins.mba.domain.release_policy import ReleaseTrigger
from src.plugins.mba.service import list_agents, load_agent
from src.plugins.mba.use_cases.emit_agent_event import EmitAgentEvent
from src.plugins.mba.domain.rollout_policy import AUDIENCES, E164_RE
from src.plugins.mba.use_cases.release_thread import ReleaseThread
from src.plugins.mba.use_cases.rollout_control import RolloutControl
from src.plugins.mba.use_cases.sync_agent import SyncAgent
from src.sdk.runtime import (
    WORKSPACE_VAULT_DIR,
    FilesystemMetadataStore,
    is_placeholder,
    mba_controls_thread,
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
        raise HTTPException(
            status_code=404, detail=f"agente MBA desconocido: {agent_id}"
        )
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
        raise HTTPException(
            status_code=404, detail=f"sesión desconocida: {session_key}"
        )
    history = data.get("control_history")
    history = (
        [h for h in history if isinstance(h, dict)] if isinstance(history, list) else []
    )
    return {
        "session_key": session_key,
        "control_owner": data.get("control_owner"),
        "control_owner_since_ms": data.get("control_owner_since_ms"),
        "control_owner_updated_at_ms": data.get("control_owner_updated_at_ms"),
        "control_owner_app_id": data.get("control_owner_app_id"),
        "thread_control": data.get("thread_control")
        if isinstance(data.get("thread_control"), dict)
        else None,
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
        phone_number_id_fallback=lambda: env_value(
            os.environ, "WHATSAPP_PHONE_NUMBER_ID"
        ),
    )


_RELEASE_GUARD_STATUS = {
    "mba_disabled": 503,
    "customer_not_enabled": 403,
    "session_unknown": 404,
}


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


# ── D1.9: agent_event hacia Meta Business Agent ─────────────────────────────


PAYLOAD_MAX_BYTES = 8 * 1024


class AgentEventBody(BaseModel):
    type: str
    order_id: str | None = Field(default=None, max_length=200)
    episode_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=DESCRIPTION_MAX)
    payload: dict[str, Any] | None = None
    source: str | None = Field(default=None, max_length=64)

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in AGENT_EVENT_TYPES:
            raise ValueError(f"type debe ser uno de {', '.join(AGENT_EVENT_TYPES)}")
        return value

    @field_validator("payload")
    @classmethod
    def _bounded_payload(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        # Viaja a Meta como string JSON dentro del evento: acotado (interno, pero sin tope = sin tope).
        if (
            value is not None
            and len(json.dumps(value, ensure_ascii=False)) > PAYLOAD_MAX_BYTES
        ):
            raise ValueError(f"payload supera {PAYLOAD_MAX_BYTES} bytes serializado")
        return value


def get_emit_agent_event() -> EmitAgentEvent:
    return EmitAgentEvent(
        metadata_store=FilesystemMetadataStore(WORKSPACE_VAULT_DIR),
        port=MetaAgentEvent(),
        is_customer_allowed=mba_customer_allowed,
        is_enabled=mba_standby_enabled,
        controls_thread=mba_controls_thread,
        entity_id_fallback=lambda: env_value(os.environ, "WHATSAPP_PHONE_NUMBER_ID"),
    )


@router.post("/sessions/{session_key}/agent-events")
async def emit_session_agent_event(
    session_key: str,
    body: AgentEventBody,
    use_case: EmitAgentEvent = Depends(get_emit_agent_event),
) -> dict[str, Any]:
    """Contarle a Meta Business Agent una novedad del pedido para que se la
    transmita al cliente (§D1.9). ``emitted=false`` con ``reason`` cuando MBA
    no controla el hilo, ya se emitió o Meta rechazó; las guardas (flag,
    lista cerrada, sesión) son 503/403/404."""
    if not _SESSION_KEY_RE.fullmatch(session_key):
        raise HTTPException(status_code=422, detail="session_key debe ser wa_<dígitos>")
    outcome = await use_case.execute(
        session_key,
        body.type,
        order_id=body.order_id,
        episode_id=body.episode_id,
        message=body.message,
        payload=body.payload,
        source=body.source,
    )
    status = _RELEASE_GUARD_STATUS.get(outcome.reason)
    if status is not None:
        raise HTTPException(status_code=status, detail=outcome.reason)
    return asdict(outcome)


# ── D2.2: sync de la configuración hacia Meta Business Agent ────────────────


_AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SYNC_GUARD_STATUS = {
    "mba_disabled": 503,
    "agent_unknown": 404,
    "sync_in_progress": 409,
    "remote_unavailable": 503,
}


class SyncBody(BaseModel):
    """``fingerprint`` (obligatorio): el del plan que el operador vio y
    confirmó; si el plan cambió entre medio, el apply se rechaza
    (``plan_changed``). No hay apply "a ciegas": es el único camino de
    escritura a la configuración de MBA y siempre pasa por la revisión."""

    fingerprint: str = Field(min_length=1, max_length=128)


def get_sync_agent() -> SyncAgent:
    from src.plugins.mba.api.connector import API_KEY_ENV

    return SyncAgent(
        admin=MetaMbaAdmin(),
        state_store=SyncStateStore(WORKSPACE_VAULT_DIR),
        load_config=load_agent,
        api_key=lambda: os.environ.get(API_KEY_ENV, ""),
        is_enabled=mba_standby_enabled,
    )


def _check_agent_id(agent_id: str) -> None:
    if not _AGENT_ID_RE.fullmatch(agent_id) or load_agent(agent_id) is None:
        raise HTTPException(
            status_code=404, detail=f"agente MBA desconocido: {agent_id}"
        )


@router.get("/agents/{agent_id}/sync")
async def get_agent_sync_state(agent_id: str) -> dict[str, Any]:
    """Último sync / intento con Meta (del vault). ``state: null`` antes del primero."""
    _check_agent_id(agent_id)
    state = SyncStateStore(WORKSPACE_VAULT_DIR).read(agent_id)
    return {"agent_id": agent_id, "state": state or None}


@router.get("/agents/{agent_id}/sync/plan")
async def get_agent_sync_plan(
    agent_id: str, use_case: SyncAgent = Depends(get_sync_agent)
) -> dict[str, Any]:
    """Solo lectura: qué cambiaría un sync (lee Meta + diff puro). No escribe."""
    _check_agent_id(agent_id)
    try:
        plan = await use_case.plan(agent_id)
    except MbaAdminError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "remote_unavailable",
                "kind": exc.kind,
                "status": exc.status,
                "detail": exc.detail,
            },
        )
    if plan is None:
        raise HTTPException(
            status_code=404, detail=f"agente MBA desconocido: {agent_id}"
        )
    return plan.summary()


@router.post("/agents/{agent_id}/sync")
async def apply_agent_sync(
    agent_id: str, body: SyncBody, use_case: SyncAgent = Depends(get_sync_agent)
) -> dict[str, Any]:
    """Aplica el plan (upserts + borrados de lo nuestro). Guardas → 503/404/409;
    ``blocked`` / ``plan_changed`` / ``nothing_to_do`` vuelven 200 con
    ``applied=false`` y su motivo, para que la tab lo muestre tal cual."""
    _check_agent_id(agent_id)
    outcome = await use_case.apply(agent_id, fingerprint=body.fingerprint)
    status = _SYNC_GUARD_STATUS.get(outcome.reason)
    if status is not None:
        raise HTTPException(
            status_code=status,
            detail={"error": outcome.reason, **(outcome.error or {})},
        )
    return asdict(outcome)


# ── D2.3: rollout — allowlist, audiencia y enabled ──────────────────────────


EVERYONE_KNOB_ENV = "MBA_ALLOW_EVERYONE"
_ROLLOUT_GUARD_STATUS = {
    "mba_disabled": 503,
    "remote_unavailable": 503,
    "unavailable": 503,
    "ambiguous": 503,
    "not_configured": 503,
    "agent_unknown": 404,
    "entity_id_missing": 409,
}
_ENTRY_ID_RE = re.compile(r"^[A-Za-z0-9_\-.:]{1,128}$")


def everyone_allowed() -> bool:
    """Knob de política: ``ai_audience=EVERYONE`` solo si el operador lo abre
    explícitamente (SSM). Placeholder / vacío / cualquier otra cosa = NO."""
    raw = os.environ.get(EVERYONE_KNOB_ENV, "")
    return not is_placeholder(raw) and raw.strip().lower() in ("1", "true", "yes", "on")


class AllowlistBody(BaseModel):
    phone: str = Field(pattern=E164_RE.pattern, max_length=16)


class AudienceBody(BaseModel):
    ai_audience: str
    confirm: bool = False

    @field_validator("ai_audience")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in AUDIENCES:
            raise ValueError(f"ai_audience debe ser uno de {list(AUDIENCES)}")
        return v


class EnabledBody(BaseModel):
    enabled: bool
    confirm: bool = False


def get_rollout_control() -> RolloutControl:
    return RolloutControl(
        admin=MetaMbaAdmin(),
        state_store=SyncStateStore(WORKSPACE_VAULT_DIR),
        load_config=load_agent,
        is_enabled=mba_standby_enabled,
        hubara_allowed=mba_customer_allowed,
        everyone_allowed=everyone_allowed,
    )


def _rollout_response(outcome: Any) -> dict[str, Any]:
    status = _ROLLOUT_GUARD_STATUS.get(outcome.reason)
    if status is not None:
        raise HTTPException(
            status_code=status,
            detail={"error": outcome.reason, **(outcome.error or {})},
        )
    return asdict(outcome)


@router.get("/agents/{agent_id}/rollout")
async def get_agent_rollout(
    agent_id: str, use_case: RolloutControl = Depends(get_rollout_control)
) -> dict[str, Any]:
    """Estado del rollout en Meta + readiness para encenderlo (solo lectura)."""
    _check_agent_id(agent_id)
    try:
        status = await use_case.status(agent_id)
    except MbaAdminError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "remote_unavailable",
                "kind": exc.kind,
                "status": exc.status,
                "detail": exc.detail,
            },
        )
    if status is None:
        raise HTTPException(
            status_code=404, detail=f"agente MBA desconocido: {agent_id}"
        )
    return asdict(status)


@router.post("/agents/{agent_id}/rollout/allowlist")
async def add_rollout_phone(
    agent_id: str,
    body: AllowlistBody,
    use_case: RolloutControl = Depends(get_rollout_control),
) -> dict[str, Any]:
    """Agrega un teléfono (E.164) a la allowlist de Meta. Solo si ya está en
    la lista cerrada de Hubara (``customer_not_in_hubara_allowlist`` si no)."""
    _check_agent_id(agent_id)
    return _rollout_response(await use_case.add_phone(agent_id, body.phone))


@router.delete("/agents/{agent_id}/rollout/allowlist/{entry_id}")
async def remove_rollout_phone(
    agent_id: str,
    entry_id: str,
    use_case: RolloutControl = Depends(get_rollout_control),
) -> dict[str, Any]:
    """Quita una entrada de la allowlist de Meta (siempre permitido)."""
    _check_agent_id(agent_id)
    if not _ENTRY_ID_RE.fullmatch(entry_id):
        raise HTTPException(status_code=422, detail="entry_id inválido")
    return _rollout_response(await use_case.remove_phone(agent_id, entry_id))


@router.put("/agents/{agent_id}/rollout/audience")
async def set_rollout_audience(
    agent_id: str,
    body: AudienceBody,
    use_case: RolloutControl = Depends(get_rollout_control),
) -> dict[str, Any]:
    """``EVERYONE`` exige el knob ``MBA_ALLOW_EVERYONE`` + ``confirm``; volver
    a ``ALLOWLISTED_ONLY`` siempre se permite."""
    _check_agent_id(agent_id)
    return _rollout_response(
        await use_case.set_audience(agent_id, body.ai_audience, confirm=body.confirm)
    )


@router.put("/agents/{agent_id}/rollout/enabled")
async def set_rollout_enabled(
    agent_id: str,
    body: EnabledBody,
    use_case: RolloutControl = Depends(get_rollout_control),
) -> dict[str, Any]:
    """Encender exige readiness completa + ``confirm`` (``not_ready`` /
    ``confirmation_required`` vuelven 200 con ``applied=false``); apagar es
    el kill switch y nunca se bloquea."""
    _check_agent_id(agent_id)
    return _rollout_response(
        await use_case.set_enabled(agent_id, body.enabled, confirm=body.confirm)
    )


# ── D2.4: consola agent_test ─────────────────────────────────────────────────


AGENT_TEST_MESSAGE_MAX = 4096
_AGENT_TEST_GUARD_STATUS = {"unavailable": 503, "not_configured": 503, "ambiguous": 503}


class AgentTestBody(BaseModel):
    message: str = Field(min_length=1, max_length=AGENT_TEST_MESSAGE_MAX)
    conversation_id: str | None = Field(default=None, max_length=128)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message no puede ser solo espacios")
        return v.strip()


def get_mba_admin() -> MbaAdminPort:
    return MetaMbaAdmin()


@router.post("/agents/{agent_id}/test")
async def agent_test(
    agent_id: str, body: AgentTestBody, admin: MbaAdminPort = Depends(get_mba_admin)
) -> dict[str, Any]:
    """Un turno del simulador de Meta (``POST /{entity_id}/agent_test``): no
    factura tokens ni toca hilos vivos. ``conversation_id`` encadena turnos.
    Meta caída / sin token → 503; un rechazo de Meta vuelve 200 ``ok=false``
    con el detalle, para que la consola lo muestre tal cual."""
    if not _AGENT_ID_RE.fullmatch(agent_id):
        raise HTTPException(
            status_code=404, detail=f"agente MBA desconocido: {agent_id}"
        )
    cfg = load_agent(agent_id)
    if cfg is None:
        raise HTTPException(
            status_code=404, detail=f"agente MBA desconocido: {agent_id}"
        )
    if not cfg.entity_id:
        raise HTTPException(status_code=409, detail={"error": "entity_id_missing"})
    try:
        reply = await admin.agent_test(
            str(cfg.entity_id), body.message, conversation_id=body.conversation_id
        )
    except MbaAdminError as exc:
        status = _AGENT_TEST_GUARD_STATUS.get(exc.kind)
        if status is not None:
            raise HTTPException(
                status_code=status,
                detail={
                    "error": "remote_unavailable",
                    "kind": exc.kind,
                    "status": exc.status,
                    "detail": exc.detail,
                },
            )
        return {
            "ok": False,
            "reply": None,
            "error": {"kind": exc.kind, "status": exc.status, "detail": exc.detail},
        }
    return {"ok": True, "reply": reply, "error": None}
