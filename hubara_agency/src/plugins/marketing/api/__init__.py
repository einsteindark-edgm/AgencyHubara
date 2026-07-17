"""API del plugin marketing — routers DELGADOS (el perfil lo audita).

La lógica vive en ``domain/`` (pura, sin I/O); este módulo traduce
HTTP ↔ dominio. Auth: la aplica el loader central (`src/main.py`) al montar
el router (require_auth salvo rutas públicas — acá no hay ninguna pública).
"""
import logging
import re
import time
import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.plugins.marketing.campaign_store import CampaignStore
from src.plugins.marketing.domain.campaigns import (
    ALL_SEGMENTS,
    STATUS_DRAFT,
    STATUS_SCHEDULED,
    campaign_stats,
    campaign_template_variables,
    customer_name_from_metadata,
    new_campaign,
    segment_for_metadata,
)
from src.plugins.marketing.domain.logic import health_payload
from src.sdk import get_task_queue
from src.sdk.connectorkit import FilesystemAttributionStore, get_catalog_client
from src.sdk.messagingkit import (
    get_current_rate_card,
    send_template_to_session,
)
from src.sdk.runtime import (
    WORKSPACE_VAULT_DIR,
    FilesystemMetadataStore,
    get_temporal_client,
)

log = logging.getLogger(__name__)

router = APIRouter()

_SEGMENT_LABELS: dict[str, tuple[str, str]] = {
    "clientes": ("Clientes", "Ya compraron · alto valor"),
    "interesados": ("Interesados", "Mostraron intención o tienen pago pendiente"),
    "frios": ("Fríos", "Consultaron sin etiqueta de compra"),
}

#: Estados en los que la campaña sigue siendo editable por el operador.
_EDITABLE_STATUSES = {STATUS_DRAFT, STATUS_SCHEDULED}


def _store() -> CampaignStore:
    return CampaignStore(WORKSPACE_VAULT_DIR)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _require_campaign(campaign_id: str) -> dict[str, Any]:
    campaign = _store().get(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    return campaign


@router.get("/health")
def health() -> dict:
    """Smoke del plugin."""
    return health_payload()


# --- CRUD -------------------------------------------------------------------


class CreateCampaignBody(BaseModel):
    name: str = Field(default="Nueva campaña sin título", max_length=120)


class MessageBody(BaseModel):
    header: str = Field(default="", max_length=60)
    body: str = Field(default="", max_length=640)
    footer: str = Field(default="", max_length=60)
    cta: str = Field(default="", max_length=20)


class UpdateCampaignBody(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    goal: str | None = None
    percent: int | None = Field(default=None, ge=0, le=100)
    coupon_code: str | None = Field(default=None, max_length=14)
    valid_until: str | None = Field(default=None, max_length=60)
    product_handle: str | None = None
    segments: list[str] | None = None
    message: MessageBody | None = None


@router.post("/campaigns", status_code=201)
def create_campaign(body: CreateCampaignBody) -> dict:
    campaign = new_campaign(
        campaign_id=f"mkt-{uuid.uuid4().hex[:10]}",
        name=body.name,
        now_ms=_now_ms(),
    )
    _store().save(campaign)
    return campaign


@router.get("/campaigns")
def list_campaigns() -> dict:
    return {"campaigns": _store().list_campaigns()}


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str) -> dict:
    return _require_campaign(campaign_id)


@router.put("/campaigns/{campaign_id}")
def update_campaign(campaign_id: str, body: UpdateCampaignBody) -> dict:
    campaign = _require_campaign(campaign_id)
    if campaign["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Campaña en estado {campaign['status']!r} — ya no es editable",
        )
    patch = body.model_dump(exclude_none=True)
    if "segments" in patch:
        unknown = set(patch["segments"]) - set(ALL_SEGMENTS)
        if unknown:
            raise HTTPException(
                status_code=422, detail=f"Segmentos desconocidos: {sorted(unknown)}"
            )
    if "coupon_code" in patch:
        patch["coupon_code"] = patch["coupon_code"].upper()
    if "message" in patch:
        patch["message"] = {**campaign["message"], **patch["message"]}
    campaign.update(patch)
    campaign["updated_at_ms"] = _now_ms()
    _store().save(campaign)
    return campaign


@router.delete("/campaigns/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: str) -> None:
    campaign = _require_campaign(campaign_id)
    if campaign["status"] != STATUS_DRAFT:
        raise HTTPException(
            status_code=409, detail="Solo se puede borrar una campaña en borrador"
        )
    _store().delete(campaign_id)


# --- Segmentos + costos -----------------------------------------------------


# --- Catálogo (picker de producto) -----------------------------------------


@router.get("/products")
async def list_products() -> dict:
    """Productos del snapshot local de catálogo, para el picker del builder."""
    result = await get_catalog_client().search("", limit=200)
    products = []
    for p in result.results:
        variant = p.variants[0] if p.variants else None
        price = variant.prices[0] if variant and variant.prices else None
        products.append(
            {
                "handle": p.handle,
                "title": p.title,
                "sku": variant.sku if variant else None,
                "category": p.categories[0] if p.categories else None,
                "price_amount": price.amount if price else None,
                "currency": price.currency_code if price else None,
                "thumbnail": p.thumbnail,
            }
        )
    return {"products": products}


# --- Enviar / programar / prueba -------------------------------------------


class SendCampaignBody(BaseModel):
    schedule_at_ms: int | None = Field(default=None, ge=0)


class TestSendBody(BaseModel):
    phone: str = Field(min_length=7, max_length=25)


def _validate_ready_to_send(campaign: dict[str, Any]) -> None:
    problems = []
    if not campaign.get("goal"):
        problems.append("falta el objetivo")
    if not (campaign.get("message") or {}).get("body"):
        problems.append("falta el cuerpo del mensaje")
    if not campaign.get("segments"):
        problems.append("falta elegir audiencia")
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))


@router.post("/campaigns/{campaign_id}/send")
async def send_campaign(campaign_id: str, body: SendCampaignBody) -> dict:
    """Envía ahora o programa (start_delay Temporal) el CampaignSendWorkflow."""
    campaign = _require_campaign(campaign_id)
    if campaign["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Campaña en estado {campaign['status']!r} — no se puede enviar",
        )
    _validate_ready_to_send(campaign)

    now = _now_ms()
    start_delay = None
    if body.schedule_at_ms is not None:
        if body.schedule_at_ms <= now:
            raise HTTPException(
                status_code=422, detail="schedule_at_ms debe estar en el futuro"
            )
        start_delay = timedelta(milliseconds=body.schedule_at_ms - now)

    client = await get_temporal_client()
    workflow_id = f"campaign-send-{campaign_id}"
    try:
        handle = await client.start_workflow(
            "CampaignSendWorkflow",
            args=[campaign_id, campaign.get("name") or campaign_id],
            id=workflow_id,
            task_queue=get_task_queue("marketing", "campaigns"),
            start_delay=start_delay,
        )
    except Exception as exc:  # noqa: BLE001 — superficie el error al operador
        log.warning("marketing.send: start_workflow falló: %s", exc)
        detail = f"No pude arrancar el envío en Temporal: {exc}"
        status = 409 if "already" in str(exc).lower() else 502
        raise HTTPException(status_code=status, detail=detail) from exc

    if body.schedule_at_ms is not None:
        campaign["status"] = STATUS_SCHEDULED
        campaign["schedule_at_ms"] = body.schedule_at_ms
        campaign["updated_at_ms"] = now
        _store().save(campaign)

    return {
        "workflow_id": workflow_id,
        "run_id": getattr(handle, "first_execution_run_id", "") or "",
        "scheduled": body.schedule_at_ms is not None,
    }


@router.post("/campaigns/{campaign_id}/cancel")
async def cancel_campaign(campaign_id: str) -> dict:
    """Cancela una campaña PROGRAMADA: aborta el workflow encolado y vuelve
    la campaña a borrador (re-programar = editar + enviar de nuevo).

    Reconciliación-lite: si Temporal ya no conoce el workflow (purga de
    namespace, deploy), igual se resetea — el estado del vault es el que ve
    el operador y no puede quedar `scheduled` huérfano.
    """
    campaign = _require_campaign(campaign_id)
    if campaign["status"] != STATUS_SCHEDULED:
        raise HTTPException(
            status_code=409,
            detail=f"Solo se cancela una campaña programada (está {campaign['status']!r})",
        )
    try:
        client = await get_temporal_client()
        await client.get_workflow_handle(f"campaign-send-{campaign_id}").cancel()
        workflow_cancelled = True
    except Exception as exc:  # noqa: BLE001 — reset igual; queda en el log
        log.warning("marketing.cancel: cancel del workflow falló: %s", exc)
        workflow_cancelled = False

    campaign["status"] = STATUS_DRAFT
    campaign["schedule_at_ms"] = None
    campaign["updated_at_ms"] = _now_ms()
    _store().save(campaign)
    return {"ok": True, "workflow_cancelled": workflow_cancelled}


def _session_id_for_phone(phone: str) -> str:
    normalized = re.sub(r"[\s\-()]", "", phone)
    return f"wa_{normalized}"


@router.post("/campaigns/{campaign_id}/test")
async def test_send(campaign_id: str, body: TestSendBody) -> dict:
    """Envío de prueba a UN número del operador, antes de disparar la campaña.

    El número debe tener sesión en el vault (haber chateado con el bot al
    menos una vez): el send resuelve phone_number_id desde su metadata.
    """
    campaign = _require_campaign(campaign_id)
    session_id = _session_id_for_phone(body.phone)
    if not (WORKSPACE_VAULT_DIR / session_id / "metadata.json").exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"El número {body.phone} no tiene conversación previa con el bot — "
                "escríbele primero al WhatsApp del negocio y reintenta"
            ),
        )
    session_metadata = FilesystemMetadataStore(WORKSPACE_VAULT_DIR).read(session_id)
    variables = campaign_template_variables(
        campaign, customer_name=customer_name_from_metadata(session_metadata)
    )
    result = await send_template_to_session(
        session_id, campaign.get("template_name") or "campaign_promo_marketing_v1", variables
    )

    campaign.setdefault("test_sends", []).append(
        {
            "phone": _session_id_for_phone(body.phone).removeprefix("wa_"),
            "at_ms": _now_ms(),
            "wa_message_id": getattr(result, "wa_message_id", None),
        }
    )
    campaign["updated_at_ms"] = _now_ms()
    _store().save(campaign)
    return {"ok": True, "session_id": session_id}


@router.get("/campaigns/{campaign_id}/stats")
def get_campaign_stats(campaign_id: str) -> dict:
    """Resultado del envío + atribución (respuestas / ventas) de la campaña."""
    campaign = _require_campaign(campaign_id)
    sessions = [
        (s.session_id, s.metadata)
        for s in FilesystemAttributionStore(WORKSPACE_VAULT_DIR).scan_sessions()
    ]
    attribution = campaign_stats(campaign, sessions)
    send_result = campaign.get("send_result") or {}
    return {
        "campaign_id": campaign_id,
        "status": campaign["status"],
        "planned": send_result.get("planned"),
        "sent": send_result.get("sent"),
        "failed_count": len(send_result.get("failed") or []),
        "skipped_count": len(send_result.get("skipped") or []),
        "unit_cost_usd_micros": send_result.get("unit_cost_usd_micros"),
        "spent_usd_micros": send_result.get("spent_usd_micros"),
        **attribution,
    }


@router.get("/segments")
def list_segments() -> dict:
    """Conteo por segmento sobre el vault + costo unitario EXPLÍCITO.

    El costo por mensaje es la tarifa marketing del rate card vigente
    (Colombia: $0.0125 USD). El frontend multiplica por la audiencia elegida.
    """
    counts = dict.fromkeys(ALL_SEGMENTS, 0)
    excluded = 0
    sessions = FilesystemAttributionStore(WORKSPACE_VAULT_DIR).scan_sessions()
    for session in sessions:
        segment = segment_for_metadata(session.metadata)
        if segment is None:
            excluded += 1
        else:
            counts[segment] += 1

    rate = get_current_rate_card().rates.get("marketing")
    unit = rate.usd_micros_per_message if rate else 0

    return {
        "segments": [
            {
                "key": key,
                "label": _SEGMENT_LABELS[key][0],
                "description": _SEGMENT_LABELS[key][1],
                "count": counts[key],
            }
            for key in ALL_SEGMENTS
        ],
        "excluded_count": excluded,
        "unit_cost_usd_micros": unit or 0,
        "currency": "USD",
    }
