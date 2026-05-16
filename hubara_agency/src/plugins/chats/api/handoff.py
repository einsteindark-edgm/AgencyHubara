"""Endpoints HTTP del dashboard para human handoff.

Cierra el círculo de la escalación humana: el operador puede tomar control
desde el dashboard, responder al cliente vía WhatsApp, y devolver el chat
al bot (Sales o Remarketing).

Tres endpoints:

  * `POST /api/dashboard/sessions/{session_id}/intervene`
      → marca `metadata.json` con `tag=HUMANO, active_route=humano` y
        termina workflows en vuelo para evitar respuesta paralela del bot.

  * `POST /api/dashboard/sessions/{session_id}/messages`
      → envía un mensaje del humano al cliente vía WhatsApp y lo persiste
        en el JSONL con `sender=human`. Sólo permitido si la ruta es humano.

  * `POST /api/dashboard/sessions/{session_id}/return-to-bot`
      → devuelve el control al bot. Para `ventas` solo marca metadata
        (próximo mensaje del cliente arranca Sales); para `remarketing`
        arranca el workflow proactivamente con un motivo del humano.

Reusan helpers extraídos en este mismo PR:
  - `send_message_to_session` (`platform/whatsapp/activities.py`)
  - `start_remarketing_for_session`, `terminate_session_workflows`
    (`platform/temporal/dispatcher.py`)
  - `FilesystemMessageHistoryStore.append_human_event`
    (`platform/session_history/store.py`)
"""
from __future__ import annotations

import time
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from src.plugins.chats.api.dashboard_composition import (
    get_history_store,
    get_metadata_store,
    get_temporal_client,
)
from src.platform.constants import (
    ROUTE_HUMANO,
    ROUTE_REMARKETING,
    ROUTE_VENTAS,
)
from src.platform.session_history import FilesystemMessageHistoryStore
from src.platform.temporal.dispatcher import (
    start_remarketing_for_session,
    terminate_session_workflows,
)
from src.platform.whatsapp.activities import send_message_to_session
from src.platform.state import FilesystemMetadataStore

logger = structlog.get_logger()

router = APIRouter()


# ---------- Request / response shapes ----------


class InterveneRequest(BaseModel):
    motivo: str | None = Field(
        default=None,
        max_length=500,
        description="Motivo opcional del humano. Si no se provee, default razonable.",
    )


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class ReturnToBotRequest(BaseModel):
    target_route: Literal["ventas", "remarketing"]
    motivo: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Requerido si target_route='remarketing' — el RemarketingWorkflow lo "
            "usa para construir el gancho de recuperación. Opcional para Sales."
        ),
    )


class HandoffResponse(BaseModel):
    ok: bool
    active_route: str
    tag: str
    motivo: str
    terminated_workflows: list[str] = Field(default_factory=list)


class HumanMessageResponse(BaseModel):
    ok: bool
    role: str
    sender: str
    content: str


# ---------- Helpers ----------


def _append_status(
    metadata: dict,
    *,
    tag: str,
    motivo: str,
    active_route: str,
    extra: dict | None = None,
) -> None:
    """Mutates `metadata` in-place: setea tag/motivo/active_route y appendea a status_history.

    Patrón idéntico al de las tools (`ManageConversationTagTool`,
    `TransferToSalesAgentTool`, `EscalateToHumanTool`) para que el dashboard
    use la misma historia continua que el frontend lee.
    """
    metadata["tag"] = tag
    metadata["motivo"] = motivo
    metadata["active_route"] = active_route

    entry = {
        "tag": tag,
        "motivo": motivo,
        "active_route": active_route,
        "timestamp": time.time(),
    }
    if extra:
        entry.update(extra)

    history = metadata.setdefault("status_history", [])
    history.append(entry)


# ---------- Endpoints ----------


@router.post(
    "/sessions/{session_id}/intervene",
    response_model=HandoffResponse,
)
async def intervene(
    session_id: Annotated[str, Path()],
    payload: InterveneRequest,
    metadata_store: Annotated[FilesystemMetadataStore, Depends(get_metadata_store)],
) -> HandoffResponse:
    """El humano toma el control de la conversación.

    Idempotente: si ya estaba en humano, lo refresca con el nuevo motivo y
    re-intenta terminar workflows zombies.
    """
    data = metadata_store.read(session_id)
    motivo = payload.motivo or "Humano tomó el control desde el dashboard"

    # 1. Marcar metadata como humano PRIMERO. Esto es lo crítico: si esto
    # falla, el endpoint 500 y el operador reintenta. Si tiene éxito, la
    # próxima webhook del cliente ya queda filtrada por LoadOrStartSalesSession
    # (route=humano → no dispatch).
    _append_status(
        data,
        tag="HUMANO",
        motivo=motivo,
        active_route=ROUTE_HUMANO,
        extra={"source": "dashboard_intervene"},
    )
    metadata_store.write(session_id, data)

    # 2. Termination de workflows en vuelo: BEST-EFFORT. Si Temporal está caído
    # o devuelve error, NO 500-amos el endpoint — la metadata ya está marcada,
    # y peor caso el bot manda UN turno más al cliente antes de quedar mudo
    # (el siguiente mensaje del cliente ya queda filtrado).
    terminated: list[str] = []
    try:
        client = await get_temporal_client()
        terminated = await terminate_session_workflows(client, session_id)
    except Exception as e:
        logger.warning(
            "dashboard.intervene: termination best-effort failed",
            session_id=session_id,
            error=str(e),
        )

    logger.info(
        "dashboard.intervene",
        session_id=session_id,
        motivo=motivo,
        terminated_workflows=terminated,
    )
    return HandoffResponse(
        ok=True,
        active_route=ROUTE_HUMANO,
        tag="HUMANO",
        motivo=motivo,
        terminated_workflows=terminated,
    )


@router.post(
    "/sessions/{session_id}/messages",
    response_model=HumanMessageResponse,
)
async def send_human_message(
    session_id: Annotated[str, Path()],
    payload: SendMessageRequest,
    metadata_store: Annotated[FilesystemMetadataStore, Depends(get_metadata_store)],
    history_store: Annotated[FilesystemMessageHistoryStore, Depends(get_history_store)],
) -> HumanMessageResponse:
    """El humano manda un mensaje al cliente desde el dashboard.

    Guarda: solo permitido si la sesión está en ruta humano. Si la ruta
    cambió (ej. el humano olvidó pulsar Intervenir o ya devolvió al bot),
    el endpoint rechaza con 409 para evitar respuestas concurrentes.
    """
    data = metadata_store.read(session_id)
    active_route = data.get("active_route", ROUTE_VENTAS)
    if active_route != ROUTE_HUMANO:
        raise HTTPException(
            status_code=409,
            detail=(
                f"La sesión {session_id} no está en ruta humano "
                f"(active_route={active_route!r}). Pulsa 'Intervenir' antes "
                "de mandar mensajes."
            ),
        )

    # 1. Mandar via WhatsApp Cloud API (puede tardar 1.5s por chunk).
    await send_message_to_session(session_id, payload.text)

    # 2. Persistir en el JSONL con `sender=human` — esto es la memoria del
    # chat que (a) el dashboard lee para mostrar al humano, (b) el LLM
    # verá al retomar el chat porque exoclaw construye el prompt desde aquí.
    history_store.append_human_event(session_id, payload.text)

    logger.info(
        "dashboard.send_human_message",
        session_id=session_id,
        chars=len(payload.text),
    )
    return HumanMessageResponse(
        ok=True,
        role="assistant",
        sender="human",
        content=payload.text,
    )


@router.post(
    "/sessions/{session_id}/return-to-bot",
    response_model=HandoffResponse,
)
async def return_to_bot(
    session_id: Annotated[str, Path()],
    payload: ReturnToBotRequest,
    metadata_store: Annotated[FilesystemMetadataStore, Depends(get_metadata_store)],
) -> HandoffResponse:
    """El humano devuelve el control al bot eligiendo Sales o Remarketing.

    - `ventas`: solo cambia metadata. El próximo mensaje del cliente arranca
      `HubaraSalesSessionWorkflow` vía `LoadOrStartSalesSession` (ruta normal).
    - `remarketing`: cambia metadata Y arranca `RemarketingWorkflow`
      inmediatamente para que el bot envíe un gancho al cliente. Requiere
      `motivo` (el workflow lo usa para construir el prompt del gancho).
    """
    data = metadata_store.read(session_id)
    active_route = data.get("active_route", ROUTE_VENTAS)
    if active_route != ROUTE_HUMANO:
        raise HTTPException(
            status_code=409,
            detail=(
                f"La sesión {session_id} no está en ruta humano "
                f"(active_route={active_route!r}); nada que devolver."
            ),
        )

    if payload.target_route == "remarketing":
        if not payload.motivo or not payload.motivo.strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    "target_route='remarketing' requiere `motivo`: el "
                    "RemarketingWorkflow lo usa para construir el gancho."
                ),
            )
        motivo = payload.motivo.strip()
        tag = "REMARKETING"
        target = ROUTE_REMARKETING
    else:
        motivo = (payload.motivo or "Humano devolvió la conversación a Sales").strip()
        tag = "RETOMA_VENTA"
        target = ROUTE_VENTAS

    _append_status(
        data,
        tag=tag,
        motivo=motivo,
        active_route=target,
        extra={"source": "dashboard_return_to_bot"},
    )
    metadata_store.write(session_id, data)

    if payload.target_route == "remarketing":
        client = await get_temporal_client()
        await start_remarketing_for_session(
            client,
            session_id=session_id,
            motivo=motivo,
            delay_seconds=0,
        )

    logger.info(
        "dashboard.return_to_bot",
        session_id=session_id,
        target_route=target,
        tag=tag,
        motivo=motivo,
    )
    return HandoffResponse(
        ok=True,
        active_route=target,
        tag=tag,
        motivo=motivo,
        terminated_workflows=[],
    )
