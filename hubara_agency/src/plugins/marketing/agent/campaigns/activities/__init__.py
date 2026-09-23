"""Activities del envío de campañas — el ÚNICO lugar del plugin con I/O.

Leen/escriben el vault vía SDK (P-28) y delegan la decisión al dominio puro
(`domain/campaigns.py`). El send real lo hace la activity de platform
`send_whatsapp_template_activity` (vía `src.sdk.messagingkit`), que ya trae
idempotencia por fingerprint, clasificación de errores Meta y persistencia
del OutboundLogEntry con su costo.
"""
import time
from datetime import datetime, timezone
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.plugins.marketing.campaign_store import CampaignStore
from src.plugins.marketing.carousel import CarouselError, resolve_campaign_carousel
from src.plugins.marketing.domain.campaigns import (
    STATUS_SENDING,
    STATUS_SENT,
    CampaignSendPlan,
    append_campaign_touch,
    build_campaign_touch,
    build_send_plan,
)
from src.sdk.connectorkit import FilesystemAttributionStore
from src.sdk.messagingkit import (
    OPT_OUT_SOURCE_META,
    CarouselCard,
    get_current_rate_card,
    is_quiet_hours_for_session,
    mark_marketing_opt_out,
)
from src.sdk.runtime import (
    WORKSPACE_VAULT_DIR,
    FilesystemMetadataStore,
)

def _now_ms() -> int:
    return int(time.time() * 1000)


def _store() -> CampaignStore:
    return CampaignStore(WORKSPACE_VAULT_DIR)


def _require_campaign(store: CampaignStore, campaign_id: str) -> dict[str, Any]:
    campaign = store.get(campaign_id)
    if campaign is None:
        raise ApplicationError(
            f"Campaña {campaign_id!r} no existe en el vault",
            non_retryable=True,
            type="CampaignNotFound",
        )
    return campaign


@activity.defn(name="load_campaign_send_plan")
async def load_campaign_send_plan_activity(campaign_id: str) -> CampaignSendPlan:
    """Resuelve audiencia real + variables + costo estimado de la campaña.

    Corre EN el momento del disparo (también para programadas): quiet hours
    se evalúa con la hora local del cliente AHORA, y la cadencia (48h entre
    campañas al mismo contacto) contra los touches vigentes.
    """
    store = _store()
    campaign = _require_campaign(store, campaign_id)
    sessions = [
        (s.session_id, s.metadata)
        for s in FilesystemAttributionStore(WORKSPACE_VAULT_DIR).scan_sessions()
    ]
    now_utc = datetime.now(timezone.utc)
    return build_send_plan(
        campaign,
        sessions,
        get_current_rate_card(),
        now_ms=int(now_utc.timestamp() * 1000),
        is_quiet_hours=lambda session_id: is_quiet_hours_for_session(
            session_id, now_utc
        ),
    )


@activity.defn(name="prepare_campaign_carousel")
async def prepare_campaign_carousel_activity(campaign_id: str) -> list[CarouselCard]:
    """Tarjetas del carrusel (product cards del catálogo de Meta), UNA vez
    por campaña. Un producto fuera del catálogo o sin META_CATALOG_ID es
    non-retryable: reintentar no lo arregla, el operador corrige.
    """
    store = _store()
    campaign = _require_campaign(store, campaign_id)
    try:
        return await resolve_campaign_carousel(campaign, now_ms=_now_ms())
    except CarouselError as e:
        raise ApplicationError(
            f"Carrusel de la campaña {campaign_id!r}: {e}",
            non_retryable=True,
            type="CampaignCarouselInvalid",
        ) from e


@activity.defn(name="mark_campaign_sending")
async def mark_campaign_sending_activity(campaign_id: str) -> None:
    store = _store()
    campaign = _require_campaign(store, campaign_id)
    campaign["status"] = STATUS_SENDING
    campaign["updated_at_ms"] = _now_ms()
    store.save(campaign)


@activity.defn(name="stamp_campaign_touch")
async def stamp_campaign_touch_activity(
    session_id: str, campaign_id: str, campaign_name: str
) -> None:
    """Estampa el identificador de campaña en el metadata del contacto.

    Es LA atribución: cuando el cliente responda, el panel de Ads agrupa la
    conversación por este touch (canal `hubara_campaign`). El nombre viaja
    denormalizado para que el reader no necesite leer `_campaigns/`.

    El touch lleva además lo que recibió el cliente (mensaje, cupón,
    productos): el bot no ve la plantilla en su historial y lo necesita
    cuando el cliente responde (bug 2026-09-22). La campaña se lee del
    vault; si ya no existe queda el touch mínimo de atribución.
    """
    campaign = _store().get(campaign_id) or {"id": campaign_id}
    campaign = {**campaign, "id": campaign_id, "name": campaign_name}
    touch = build_campaign_touch(campaign, sent_at_ms=_now_ms())

    def _append_touch(metadata: dict[str, Any]) -> dict[str, Any]:
        return append_campaign_touch(metadata, touch)

    # `update` (flock por sesión): el ingest del webhook escribe el mismo
    # metadata.json — un read→write suelto perdería updates concurrentes.
    FilesystemMetadataStore(WORKSPACE_VAULT_DIR).update(session_id, _append_touch)


@activity.defn(name="mark_marketing_opt_out")
async def mark_marketing_opt_out_activity(session_id: str, campaign_id: str) -> None:
    """Meta rechazó el envío con 131050: el cliente eligió, desde WhatsApp,
    no recibir más marketing del negocio. Es una baja, no un fallo: queda
    registrada (fecha, vía "meta", ESTA campaña como la que la provocó) y la
    audiencia de las próximas campañas lo excluye. Sticky como la baja por
    texto: si ya estaba de baja no se pisa el registro."""
    now = _now_ms()

    def _mark(metadata: dict[str, Any]) -> dict[str, Any]:
        return mark_marketing_opt_out(
            metadata, now_ms=now, source=OPT_OUT_SOURCE_META, campaign_id=campaign_id
        )

    FilesystemMetadataStore(WORKSPACE_VAULT_DIR).update(session_id, _mark)


@activity.defn(name="record_campaign_send_result")
async def record_campaign_send_result_activity(
    campaign_id: str, result: dict[str, Any]
) -> None:
    """Persiste el resultado del envío — el historial queda en el vault."""
    store = _store()
    campaign = _require_campaign(store, campaign_id)
    now = _now_ms()
    campaign["status"] = STATUS_SENT
    campaign["sent_at_ms"] = now
    campaign["updated_at_ms"] = now
    campaign["send_result"] = result
    store.save(campaign)


__all__ = (
    "load_campaign_send_plan_activity",
    "mark_campaign_sending_activity",
    "mark_marketing_opt_out_activity",
    "prepare_campaign_carousel_activity",
    "record_campaign_send_result_activity",
    "stamp_campaign_touch_activity",
)
