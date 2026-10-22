"""Activities del envío de campañas — el ÚNICO lugar del plugin con I/O.

Leen/escriben el vault vía SDK (P-28) y delegan la decisión al dominio puro
(`domain/campaigns.py`). El send real lo hace la activity de platform
`send_whatsapp_template_activity` (vía `src.sdk.messagingkit`), que ya trae
idempotencia por fingerprint, clasificación de errores Meta y persistencia
del OutboundLogEntry con su costo.
"""
import asyncio
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.plugins.marketing.campaign_coupon import check_campaign_coupon
from src.plugins.marketing.campaign_send_store import CampaignSendStore
from src.plugins.marketing.campaign_store import CampaignStore
from src.plugins.marketing.carousel import CarouselError, resolve_campaign_carousel
from src.plugins.marketing.domain.campaign_send import (
    DELIVERY_OPTED_OUT,
    DELIVERY_SENT,
    DELIVERY_SKIPPED,
    CampaignBatch,
    CampaignBatchResult,
    CampaignDelivery,
    CampaignDeliveryOutcome,
    CampaignSendProgress,
    CampaignSendStart,
    campaign_send_result,
    recheck_batch,
)
from src.plugins.marketing.domain.campaigns import (
    SKIP_SESION_DE_PRUEBA,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_SENDING,
    STATUS_SENT,
    CampaignSendPlan,
    RecipientSendPlan,
    append_campaign_touch,
    block_campaign_send,
    blocked_send_plan,
    build_campaign_touch,
    build_send_plan,
    campaign_touch_of,
)
from src.plugins.marketing.domain.coupons import coupon_terms
from src.sdk.connectorkit import FilesystemAttributionStore
from src.sdk.messagingkit import (
    OPT_OUT_SOURCE_META,
    CarouselCard,
    get_current_rate_card,
    is_meta_opt_out_failure,
    is_quiet_hours_for_session,
    mark_marketing_opt_out,
    send_template_to_session,
)
from src.sdk.runtime import (
    WORKSPACE_VAULT_DIR,
    FilesystemMetadataStore,
    with_heartbeat,
)

#: Intentos de `load_campaign_send_plan` (el workflow usa este número): en el
#: último, un Medusa que no responde ya no se reintenta — el envío se frena
#: con el motivo a la vista.
PLAN_MAX_ATTEMPTS = 3
#: Tope de la validación del cupón: la activity termina (y deja el motivo)
#: antes de su propio timeout aunque Medusa se cuelgue.
_COUPON_CHECK_TIMEOUT_S = 15


def _now_ms() -> int:
    return int(time.time() * 1000)


def _store() -> CampaignStore:
    return CampaignStore(WORKSPACE_VAULT_DIR)


def get_promotions_port() -> Any:
    """Provider a nivel módulo (lazy + monkeypatcheable en tests): el import
    arrastra la composición de Medusa (gate `test_sdk_lazy_surface`)."""
    from src.sdk.connectorkit import get_promotions_port as _factory

    return _factory()


async def _coupon_blocks_send(store: CampaignStore, campaign: dict[str, Any], *, now_ms: int) -> str | None:
    """Re-valida el cupón AL DISPARAR: una campaña programada guardó los
    términos de cuando se programó, y el cupón pudo pausarse, vencer o
    cambiar de %. Si ya no sirve, el envío NO sale y la campaña queda
    fallida con el motivo; si sirve, se copian sus términos de ahora (% y
    "válido hasta"). Devuelve el motivo, o None si el envío sigue."""
    from src.sdk.connectorkit import NullPromotionsPort, PromotionsUnavailableError

    code = (campaign.get("coupon_code") or "").strip()
    if not code:
        return None
    port = get_promotions_port()
    if isinstance(port, NullPromotionsPort):
        # Config del worker, no del cupón: reintentar no lo arregla.
        return _block(store, campaign, now_ms, (
            f"No se envió: el worker de campañas no tiene Medusa configurado para "
            f"validar el cupón {code}."
        ))
    # El port del proceso cachea 60 s: un cupón pausado justo antes del
    # disparo no puede colarse por el cache (se lee Medusa fresca).
    invalidate = getattr(port, "invalidate", None)
    if callable(invalidate):
        invalidate()
    try:
        check = await asyncio.wait_for(
            check_campaign_coupon(port, campaign, at_ms=now_ms), timeout=_COUPON_CHECK_TIMEOUT_S
        )
    except (PromotionsUnavailableError, TimeoutError) as exc:
        if activity.info().attempt < PLAN_MAX_ATTEMPTS:
            raise ApplicationError(
                f"No pude validar el cupón {code} en Medusa: {exc}", type="CampaignCouponUnavailable"
            ) from exc
        return _block(store, campaign, now_ms, (
            f"No se envió: no pude validar el cupón {code} en Medusa a la hora del envío (no respondió)."
        ))
    if check is None:
        return None
    if check.problem:
        return _block(store, campaign, now_ms, f"No se envió: el cupón {code} {check.problem}.")
    terms = coupon_terms(check.promotion)
    if any(campaign.get(k) != v for k, v in terms.items()):
        campaign.update(terms)
        campaign["updated_at_ms"] = now_ms
        store.save(campaign)
    return None


def _block(store: CampaignStore, campaign: dict[str, Any], now_ms: int, reason: str) -> str:
    activity.logger.warning("campaña %s frenada: %s", campaign.get("id"), reason)
    store.save(block_campaign_send(campaign, reason, now_ms=now_ms))
    return reason


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
@with_heartbeat(every=10)
async def load_campaign_send_plan_activity(campaign_id: str) -> CampaignSendPlan:
    """Resuelve audiencia real + variables + costo estimado de la campaña.

    Corre EN el momento del disparo (también para programadas): el cupón se
    re-valida AHORA (si ya no sirve, el plan sale frenado con el motivo),
    quiet hours se evalúa con la hora local del cliente AHORA, y la cadencia
    (48h entre campañas al mismo contacto) contra los touches vigentes.
    """
    return await _build_send_plan(campaign_id)


async def _build_send_plan(campaign_id: str) -> CampaignSendPlan:
    store = _store()
    campaign = _require_campaign(store, campaign_id)
    now_utc = datetime.now(timezone.utc)
    now_ms = int(now_utc.timestamp() * 1000)
    blocked = await _coupon_blocks_send(store, campaign, now_ms=now_ms)
    if blocked is not None:
        return blocked_send_plan(campaign, blocked)
    # Escanear el vault es I/O síncrono largo: en un hilo, para no congelar el
    # loop del worker (los envíos a Meta y el heartbeat corren en él).
    return await asyncio.to_thread(_plan_over_the_vault, campaign, now_utc, now_ms)


def _plan_over_the_vault(
    campaign: dict[str, Any], now_utc: datetime, now_ms: int
) -> CampaignSendPlan:
    sessions = [
        (s.session_id, s.metadata)
        for s in FilesystemAttributionStore(WORKSPACE_VAULT_DIR).scan_sessions()
    ]
    return build_send_plan(
        campaign,
        sessions,
        get_current_rate_card(),
        now_ms=now_ms,
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
    session_id: str, campaign_id: str, campaign_name: str, wa_message_id: str | None = None
) -> None:
    """Estampa el identificador de campaña en el metadata del contacto.

    Es LA atribución: cuando el cliente responda, el panel de Ads agrupa la
    conversación por este touch (canal `hubara_campaign`). El nombre viaja
    denormalizado para que el reader no necesite leer `_campaigns/`.

    El touch lleva además lo que recibió el cliente (mensaje, cupón,
    productos): el bot no ve la plantilla en su historial y lo necesita
    cuando el cliente responde (bug 2026-09-22). La campaña se lee del
    vault; si ya no existe queda el touch mínimo de atribución.

    `wa_message_id`: el id del mensaje que devolvió Meta. Con él, el webhook
    de estados anota en este touch si se entregó, si se leyó y cuánto costó:
    las métricas de envío de la campaña en Ads (2026-09-25).
    """
    campaign = _store().get(campaign_id) or {"id": campaign_id}
    campaign = {**campaign, "id": campaign_id, "name": campaign_name}
    touch = build_campaign_touch(campaign, sent_at_ms=_now_ms(), wa_message_id=wa_message_id)

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


# --- Envío por lotes (CampaignSendWorkflowV2) --------------------------------


#: Razones de skip que no son de la campaña (el resto del vault).
_NOT_THIS_CAMPAIGN = frozenset({"fuera_de_segmento", SKIP_SESION_DE_PRUEBA})


def _send_store() -> CampaignSendStore:
    return CampaignSendStore(WORKSPACE_VAULT_DIR)


@activity.defn(name="prepare_campaign_send")
@with_heartbeat(every=10)
async def prepare_campaign_send_activity(campaign_id: str) -> CampaignSendStart:
    """Arma el plan (mismas reglas que el V1, AL DISPARAR) y lo guarda en el
    vault: el workflow recibe solo el conteo — el plan de miles de contactos
    no cabe en un payload de Temporal (2 MB)."""
    plan = await _build_send_plan(campaign_id)
    # Los «fuera de segmento» son el resto del vault y las sesiones de prueba
    # ni son contactos: no son de esta campaña (inflaban el resultado).
    plan = replace(
        plan, skipped=[s for s in plan.skipped if s.reason not in _NOT_THIS_CAMPAIGN]
    )
    if plan.blocked_reason:
        return CampaignSendStart(
            total=0, template_name=plan.template_name, blocked_reason=plan.blocked_reason
        )
    await asyncio.to_thread(_send_store().save_plan, plan)
    return CampaignSendStart(
        total=len(plan.recipients),
        template_name=plan.template_name,
        carousel=bool(plan.carousel_handles),
    )


@activity.defn(name="load_campaign_batch")
async def load_campaign_batch_activity(campaign_id: str, start: int, end: int) -> CampaignBatch:
    """Los destinatarios `[start, end)` del plan, re-chequeados AHORA: una
    baja, un caso que pasó a un humano o el horario de silencio del cliente
    desde que se armó el plan lo frenan (con su razón). El plan entero y el
    metadata de cada contacto se leen en un hilo (no congelan el worker)."""
    return await asyncio.to_thread(_load_batch, campaign_id, start, end)


def _load_batch(campaign_id: str, start: int, end: int) -> CampaignBatch:
    try:
        recipients = _send_store().recipients(campaign_id, start, end)
    except FileNotFoundError as e:
        raise ApplicationError(
            f"El plan del envío de la campaña {campaign_id!r} no está en el vault",
            non_retryable=True,
            type="CampaignSendPlanMissing",
        ) from e
    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    now_utc = datetime.now(timezone.utc)
    batch = recheck_batch(
        recipients,
        {r.session_id: metadata_store.read(r.session_id) for r in recipients},
        campaign_id=campaign_id,
        is_quiet_hours=lambda session_id: is_quiet_hours_for_session(session_id, now_utc),
        now_ms=int(now_utc.timestamp() * 1000),
    )
    # Lo que recibe el cliente, UNA vez por lote (el touch de cada envío lo
    # copia): el bot lo necesita cuando el cliente responde (bug 2026-09-22).
    campaign = _store().get(campaign_id) or {"id": campaign_id}
    touch = build_campaign_touch({**campaign, "id": campaign_id}, sent_at_ms=0)
    quiet = [s.session_id for s in batch.skipped if s.reason == "quiet_hours"]
    quiet_until_ms = None
    if recipients and len(quiet) * 2 >= len(recipients):
        # La mayoría fuera de horario: el lote espera a que abra la ventana
        # en vez de quemarlos como omitidos.
        quiet_until_ms = max(_window_opens_ms(sid, now_utc) for sid in quiet)
    return replace(batch, touch=touch, quiet_until_ms=quiet_until_ms)


#: Paso con el que se busca cuándo abre el horario del cliente, y el tope.
_WINDOW_STEP = timedelta(minutes=15)
_WINDOW_HORIZON = timedelta(hours=24)


def _window_opens_ms(session_id: str, now_utc: datetime) -> int:
    """Cuándo sale el contacto de su horario de silencio (precisión 15 min),
    con el mismo predicado que decide si está en silencio."""
    probe = now_utc
    while probe - now_utc < _WINDOW_HORIZON:
        probe += _WINDOW_STEP
        if not is_quiet_hours_for_session(session_id, probe):
            break
    return int(probe.timestamp() * 1000)


@activity.defn(name="deliver_campaign_message")
@with_heartbeat(every=10)
async def deliver_campaign_message_activity(delivery: CampaignDelivery) -> CampaignDeliveryOutcome:
    """Entrega la campaña a UN contacto: re-chequea, envía y deja el touch.

    Corre en el queue de envíos (con su ritmo hacia Meta), así que puede
    esperar turno: la baja, el caso en manos de un humano y el horario de
    silencio se miran AL MOMENTO de mandar. Si el contacto ya tiene el touch
    real de esta campaña (un reintento después de enviar y anotar, un lote
    que se volvió a correr), no se reenvía. El touch es de mejor esfuerzo: lo
    que ya salió nunca se reintenta por no poder anotarlo (lo reenviaría).
    """
    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    session_id = delivery.session_id
    metadata = await asyncio.to_thread(metadata_store.read, session_id)
    already = campaign_touch_of(metadata, delivery.campaign_id)
    if already is not None:
        return CampaignDeliveryOutcome(
            outcome=DELIVERY_SENT, wa_message_id=already.get("wa_message_id")
        )
    now_utc = datetime.now(timezone.utc)
    recheck = recheck_batch(
        [
            RecipientSendPlan(
                session_id=session_id,
                variables=delivery.variables,
                bypass_cooldown=delivery.bypass_cooldown,
            )
        ],
        {session_id: metadata},
        campaign_id=delivery.campaign_id,
        is_quiet_hours=lambda sid: is_quiet_hours_for_session(sid, now_utc),
        now_ms=int(now_utc.timestamp() * 1000),
    )
    if recheck.skipped:
        return CampaignDeliveryOutcome(
            outcome=DELIVERY_SKIPPED, skip_reason=recheck.skipped[0].reason
        )
    try:
        result = await send_template_to_session(
            session_id,
            delivery.template_name,
            delivery.variables,
            carousel_cards=delivery.cards or None,
        )
    except ApplicationError as e:
        if not is_meta_opt_out_failure(e.type):
            raise
        # Baja hecha en WhatsApp (131050): se registra atribuida a ESTA
        # campaña; no es un fallo ni se le vuelve a intentar.
        await _best_effort(
            "baja", session_id, metadata_store.update, session_id, _opt_out_mutator(delivery.campaign_id)
        )
        return CampaignDeliveryOutcome(outcome=DELIVERY_OPTED_OUT)
    # El id del mensaje va al touch: por él el webhook de estados anota
    # entregado, leído y precio de ESTA campaña (Ads, 2026-09-25).
    wa_message_id = getattr(result, "wa_message_id", None) or None
    touch = {**delivery.touch, "sent_at_ms": _now_ms()}
    if wa_message_id:
        touch["wa_message_id"] = wa_message_id
    stamped = await _best_effort(
        "touch",
        session_id,
        metadata_store.update,
        session_id,
        lambda metadata: append_campaign_touch(metadata, touch),
    )
    return CampaignDeliveryOutcome(
        outcome=DELIVERY_SENT, wa_message_id=wa_message_id, stamped=stamped
    )


def _opt_out_mutator(campaign_id: str) -> Any:
    now = _now_ms()

    def _mark(metadata: dict[str, Any]) -> dict[str, Any]:
        return mark_marketing_opt_out(
            metadata, now_ms=now, source=OPT_OUT_SOURCE_META, campaign_id=campaign_id
        )

    return _mark


async def _best_effort(what: str, session_id: str, write: Any, *args: Any) -> bool:
    """Una escritura al vault de algo que YA pasó en Meta: si falla, queda en
    el log y el envío sigue contando (nunca se reintenta por eso)."""
    try:
        await asyncio.to_thread(write, *args)
    except Exception:  # noqa: BLE001 — mejor esfuerzo, a la vista en el log
        activity.logger.exception("campaña: no pude anotar %s de %s", what, session_id)
        return False
    return True


@activity.defn(name="record_campaign_progress")
async def record_campaign_progress_activity(
    campaign_id: str, progress: CampaignSendProgress, batch: CampaignBatchResult | None = None
) -> None:
    """El avance a la vista del operador (estado + conteos), y el resultado
    del lote para el resumen final. Idempotente: reintentar pisa lo mismo."""
    if batch is not None:
        _send_store().save_batch(campaign_id, batch)
    store = _store()
    campaign = _require_campaign(store, campaign_id)
    campaign["status"] = STATUS_PAUSED if progress.paused else STATUS_SENDING
    campaign["progress"] = asdict(progress)
    campaign["updated_at_ms"] = _now_ms()
    store.save(campaign)


@activity.defn(name="finish_campaign_send")
async def finish_campaign_send_activity(
    campaign_id: str, progress: CampaignSendProgress, outcome: str, reason: str | None = None
) -> None:
    """Cierra la campaña: enviada, cancelada (con lo que alcanzó a salir) o
    fallida con el motivo. El resultado completo sale de los lotes guardados;
    después el plan y los lotes se borran del vault."""
    send_store = _send_store()
    store = _store()
    campaign = _require_campaign(store, campaign_id)
    if (
        not send_store.exists(campaign_id)
        and campaign.get("status") == outcome
        and campaign.get("send_result") is not None
    ):
        # Reintento de un cierre que ya se guardó (y ya borró el plan): no se
        # pisa el resultado con uno vacío.
        return
    result = await asyncio.to_thread(
        lambda: campaign_send_result(
            send_store.find_plan(campaign_id), send_store.batches(campaign_id)
        )
    )
    now = _now_ms()
    campaign["status"] = outcome
    if progress.sent > 0:
        # Fecha de envío solo si algo salió (fallida antes del primer envío: no).
        campaign["sent_at_ms"] = now
    campaign["updated_at_ms"] = now
    campaign["progress"] = {**asdict(progress), "state": outcome}
    campaign["send_result"] = result
    if outcome == STATUS_FAILED or reason:
        # También al cancelar: una pausa olvidada que se canceló sola o una
        # cancelación desde Temporal dicen por qué.
        campaign["failure_reason"] = reason
    store.save(campaign)
    activity.logger.info(
        "campaña %s cerrada: %s — %d de %d procesados, %d enviados%s",
        campaign_id,
        outcome,
        progress.processed,
        progress.total,
        progress.sent,
        f" — {reason}" if reason else "",
    )
    send_store.remove(campaign_id)


__all__ = (
    "deliver_campaign_message_activity",
    "finish_campaign_send_activity",
    "load_campaign_batch_activity",
    "prepare_campaign_send_activity",
    "record_campaign_progress_activity",
    "load_campaign_send_plan_activity",
    "mark_campaign_sending_activity",
    "mark_marketing_opt_out_activity",
    "prepare_campaign_carousel_activity",
    "record_campaign_send_result_activity",
    "stamp_campaign_touch_activity",
)
