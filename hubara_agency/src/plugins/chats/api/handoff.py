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
import uuid
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile
from pydantic import BaseModel, Field, model_validator

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
from src.platform.media.store import media_url_for, persist_outbound_image
from src.platform.session_history import FilesystemMessageHistoryStore
from src.platform.temporal.dispatcher import (
    start_remarketing_for_session,
    terminate_session_workflows,
)
from src.platform.whatsapp.activities import (
    send_image_to_session,
    send_message_to_session,
)
from src.platform.whatsapp.client import MediaUploadError, upload_media
from src.platform.whatsapp.window import is_service_window_closed
from src.platform.state import FilesystemMetadataStore

#: Mimes de imagen que WhatsApp renderiza en `type=image`. Otros (gif, webp)
#: Meta los trata distinto (sticker/animación) — fuera de scope: solo fotos.
_ALLOWED_IMAGE_MIMES: frozenset[str] = frozenset({"image/jpeg", "image/png"})

#: Límite de tamaño de imagen post-compresión (5 MB — el máximo de Meta para
#: `type=image`). El frontend comprime antes de subir, así que en la práctica
#: rara vez se acerca; esto es la red de seguridad server-side.
_MAX_IMAGE_BYTES: int = 5 * 1024 * 1024

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
    """Mensaje del operador humano. Al menos uno de `text` / `attachment_id`.

    * `text` solo → mensaje de texto (path legacy, sin cambios).
    * `attachment_id` → media_id de Meta devuelto por `POST .../media` (fase A);
      `text` opcional pasa a ser el caption de la foto.
    * `client_message_id` → UUID del cliente para idempotencia: un retry con el
      mismo id NO re-envía a WhatsApp (dedup real, no solo de UI).
    """

    text: str | None = Field(default=None, max_length=4096)
    attachment_id: str | None = Field(default=None, max_length=256)
    client_message_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _text_or_attachment(self) -> SendMessageRequest:
        has_text = bool(self.text and self.text.strip())
        if not has_text and not self.attachment_id:
            raise ValueError("se requiere `text` o `attachment_id`")
        return self


class MediaUploadResponse(BaseModel):
    ok: bool
    attachment_id: str  # media_id de Meta — se referencia en el send
    media_ref: str  # url servible por el dashboard (GET /api/dashboard/media/...)


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
    image_url: str | None = None


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


def _running_watchdog_ids(data: dict, session_id: str) -> list[str]:
    """Best-effort: workflow ids del watchdog per-episodio para esta sesión.

    El watchdog de ventana 24h corre como `watchdog-{session_id}-{episode_id}`
    y NO sigue el prefijo `session-`/`remarketing-`, así que `terminate_session_
    workflows` no lo descubre solo. Cuando el humano toma el control lo cerramos
    también para que un template de re-engagement no se dispare sobre el operador.

    Defense-in-depth ya hace que el watchdog se auto-skipee con
    `active_route=humano` (ver `check_watchdog_eligibility_activity`); terminarlo
    explícitamente solo libera de inmediato el workflow que quedaría colgado
    durmiendo hasta que cierre la ventana.

    Devuelve `[]` ante cualquier metadata ausente/malformada — nunca rompe el
    take-over.
    """
    ids: list[str] = []

    # 1. Id autoritativo si el orquestador lo persistió en metadata.watchdog.
    watchdog = data.get("watchdog") or {}
    wid = watchdog.get("workflow_id")
    if isinstance(wid, str) and wid:
        ids.append(wid)

    # 2. Id determinístico desde el episodio activo (último, no cerrado) — es
    #    como lo construye el ingest al emitir `ServiceWindowOpenedEvent`.
    episodes = data.get("episodes") or []
    if episodes:
        active_ep = episodes[-1]
        if isinstance(active_ep, dict) and active_ep.get("closed_at_ms") is None:
            episode_id = active_ep.get("episode_id")
            if isinstance(episode_id, str) and episode_id:
                candidate = f"watchdog-{session_id}-{episode_id}"
                if candidate not in ids:
                    ids.append(candidate)

    return ids


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
        terminated = await terminate_session_workflows(
            client,
            session_id,
            extra_workflow_ids=_running_watchdog_ids(data, session_id),
        )
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


def _require_humano_route(data: dict, session_id: str, verb: str) -> None:
    """409 si la sesión no está en ruta humano (evita respuestas concurrentes)."""
    active_route = data.get("active_route", ROUTE_VENTAS)
    if active_route != ROUTE_HUMANO:
        raise HTTPException(
            status_code=409,
            detail=(
                f"La sesión {session_id} no está en ruta humano "
                f"(active_route={active_route!r}). Pulsa 'Intervenir' antes "
                f"de {verb}."
            ),
        )


@router.post(
    "/sessions/{session_id}/media",
    response_model=MediaUploadResponse,
)
async def upload_human_media(
    session_id: Annotated[str, Path()],
    metadata_store: Annotated[FilesystemMetadataStore, Depends(get_metadata_store)],
    file: Annotated[UploadFile, File()],
) -> MediaUploadResponse:
    """Fase A del envío de foto: el operador sube el archivo.

    Persistimos la foto en el vault (para re-renderizarla en el histórico) y la
    subimos a Meta (`upload_media`) para obtener un `media_id`. Devolvemos el
    `attachment_id` (= media_id) + `media_ref` (url servible). El envío al
    cliente es una llamada SEPARADA (`POST .../messages` con `attachment_id`),
    para que un retry del send nunca re-suba los bytes.
    """
    data = metadata_store.read(session_id)
    _require_humano_route(data, session_id, "subir fotos")

    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in _ALLOWED_IMAGE_MIMES:
        raise HTTPException(
            status_code=415,
            detail=f"Tipo no soportado: {mime!r}. Solo JPEG o PNG.",
        )

    content = await file.read()
    if len(content) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Imagen demasiado grande ({len(content)} bytes; máx {_MAX_IMAGE_BYTES}).",
        )

    # 1. Persistir en disco (token opaco → filename `out-...`).
    filename = persist_outbound_image(session_id, content, mime, token=uuid.uuid4().hex)
    media_ref = media_url_for(session_id, filename)

    # 2. Subir a Meta. Falla → 502 (frontend reintenta SOLO la subida).
    try:
        media_id = await upload_media(data.get("phone_number_id") or _env_phone(), content, mime)
    except MediaUploadError as e:
        logger.error("dashboard.upload_media failed", session_id=session_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"WhatsApp media upload falló: {e}")

    # 3. Registrar el mapping media_id → media_ref para que el send (fase B)
    #    persista la foto en el histórico sin confiar en el frontend.
    outbound = data.setdefault("outbound_media", {})
    outbound[media_id] = {"media_ref": media_ref, "filename": filename, "mime": mime}
    metadata_store.write(session_id, data)

    logger.info(
        "dashboard.upload_human_media",
        session_id=session_id,
        bytes=len(content),
        media_id=media_id,
    )
    return MediaUploadResponse(ok=True, attachment_id=media_id, media_ref=media_ref)


def _env_phone() -> str:
    import os

    phone = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not phone:
        raise HTTPException(status_code=500, detail="WHATSAPP_PHONE_NUMBER_ID no configurado")
    return phone


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
    """El humano manda un mensaje (texto y/o foto) al cliente desde el dashboard.

    Guards:
      * ruta humano (409) — evita respuestas concurrentes humano + bot.
      * ventana de servicio 24h (409) — si SABEMOS que cerró, cortamos antes de
        que Meta rechace el free-form en silencio (fix del bug latente). Si la
        metadata de ventana no está poblada, no bloquea (fail-open).
      * idempotencia por `client_message_id` — un retry no re-envía.
    """
    data = metadata_store.read(session_id)
    _require_humano_route(data, session_id, "mandar mensajes")

    # Guard de ventana 24h — solo bloquea si sabemos que cerró.
    if is_service_window_closed(int(time.time() * 1000), data):
        raise HTTPException(
            status_code=409,
            detail=(
                "La ventana de servicio de 24h de WhatsApp está cerrada: no se "
                "puede mandar un mensaje libre. El cliente debe escribir primero, "
                "o hay que usar una plantilla aprobada."
            ),
        )

    # Idempotencia: si este client_message_id ya se procesó, replay sin re-enviar.
    cmid = payload.client_message_id
    processed: list[str] = data.get("sent_human_message_ids", [])
    if cmid and cmid in processed:
        logger.info(
            "dashboard.send_human_message replay (idempotent)",
            session_id=session_id,
            client_message_id=cmid,
        )
        return HumanMessageResponse(
            ok=True, role="assistant", sender="human", content=payload.text or ""
        )

    caption = payload.text.strip() if payload.text and payload.text.strip() else None
    image_ref: str | None = None

    if payload.attachment_id:
        # Envío de FOTO (fase B). El media_id ya vive en Meta (fase A).
        image_ref = (data.get("outbound_media", {}).get(payload.attachment_id) or {}).get(
            "media_ref"
        )
        result = await send_image_to_session(
            session_id, media_id=payload.attachment_id, caption=caption
        )
        if not result.ok:
            raise HTTPException(
                status_code=502, detail=f"WhatsApp rechazó la foto: {result.error}"
            )
        history_store.append_human_event(session_id, caption or "", image_url=image_ref)
    else:
        # Envío de TEXTO (path legacy).
        await send_message_to_session(session_id, payload.text)
        history_store.append_human_event(session_id, payload.text)

    # Marcar el client_message_id como procesado (idempotencia futura).
    if cmid:
        processed.append(cmid)
        data["sent_human_message_ids"] = processed[-200:]  # cap para no crecer sin límite
        metadata_store.write(session_id, data)

    logger.info(
        "dashboard.send_human_message",
        session_id=session_id,
        has_image=bool(payload.attachment_id),
        chars=len(payload.text or ""),
    )
    return HumanMessageResponse(
        ok=True,
        role="assistant",
        sender="human",
        content=caption or (payload.text or ""),
        image_url=image_ref,
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
