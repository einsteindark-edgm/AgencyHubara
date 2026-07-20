"""Dominio de campañas de marketing directo por WhatsApp — lógica PURA.

Sin I/O, sin Temporal, sin vendors: los callers (activities/api) leen el
vault y pasan dicts; acá solo se decide. Los tags del clasificador son el
vocabulario vivo del sistema (``src.sdk.messagingkit``): COMPRA_EXITOSA /
INTERESADO / CONFIRMADO_PAGO_PENDIENTE / HUMANO / NO_ETIQUETADO.
"""
from dataclasses import dataclass
from typing import Any

SEGMENT_CLIENTES = "clientes"
SEGMENT_INTERESADOS = "interesados"
SEGMENT_FRIOS = "frios"

#: Contacto agregado a mano por el operador (fuera de los segmentos elegidos).
SEGMENT_MANUAL = "manual"

ALL_SEGMENTS: tuple[str, ...] = (
    SEGMENT_CLIENTES,
    SEGMENT_INTERESADOS,
    SEGMENT_FRIOS,
)

_TAG_TO_SEGMENT: dict[str, str] = {
    "COMPRA_EXITOSA": SEGMENT_CLIENTES,
    "INTERESADO": SEGMENT_INTERESADOS,
    "CONFIRMADO_PAGO_PENDIENTE": SEGMENT_INTERESADOS,
}


_TAG_HUMAN = "HUMANO"


def segment_for_metadata(metadata: dict[str, Any]) -> str | None:
    """Segmento de audiencia del contacto, o None si está excluido.

    Exclusiones (jamás reciben campañas): caso en manos de un humano
    (tag o route — invariante tag=HUMANO ⇔ route=humano) y opt-out de
    marketing (el template promete la baja; acá se honra).
    """
    if metadata.get("tag") == _TAG_HUMAN or metadata.get("active_route") == "humano":
        return None
    if metadata.get("marketing_opt_out"):
        return None
    return _TAG_TO_SEGMENT.get(metadata.get("tag"), SEGMENT_FRIOS)


CATEGORY_MARKETING = "marketing"

STATUS_DRAFT = "draft"
STATUS_SCHEDULED = "scheduled"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"

#: Template MARKETING pre-aprobado por Meta que viaja en cada campaña
#: (ver `platform/whatsapp/templates/catalog.yaml` + provisioning).
CAMPAIGN_TEMPLATE_NAME = "campaign_promo_marketing_v1"


@dataclass(frozen=True)
class CampaignRecipient:
    """Un destinatario resuelto (R-JSON: cruza activity↔workflow)."""

    session_id: str
    segment: str
    customer_name: str | None = None


@dataclass(frozen=True)
class SkippedRecipient:
    session_id: str
    reason: str


@dataclass(frozen=True)
class CampaignAudience:
    recipients: list[CampaignRecipient]
    skipped: list[SkippedRecipient]


#: Respiro mínimo entre campañas al MISMO contacto (calidad del número Meta:
#: dos promos en el mismo día invitan al report/block).
CAMPAIGN_COOLDOWN_MS = 48 * 60 * 60 * 1000


def _has_recent_campaign_touch(metadata: dict[str, Any], now_ms: int) -> bool:
    for touch in metadata.get("campaign_touches") or []:
        if not isinstance(touch, dict):
            continue
        sent = touch.get("sent_at_ms")
        if isinstance(sent, int) and now_ms - sent < CAMPAIGN_COOLDOWN_MS:
            return True
    return False


def resolve_campaign_audience(
    campaign: dict[str, Any],
    sessions: list[tuple[str, dict[str, Any]]],
    *,
    now_ms: int | None = None,
    is_quiet_hours: Any = None,
) -> CampaignAudience:
    """Resuelve la audiencia REAL de la campaña sobre los metadata del vault.

    Cada sesión cae en exactamente uno de: recipient (su segmento está en la
    campaña), o skipped con razón: "excluido" (HUMANO / opt-out),
    "quiet_hours" (hora local del cliente fuera de horario — el predicado lo
    inyecta la activity, que corre EN el momento del disparo, también para
    programadas), "campana_reciente" (touch de campaña < 48h — respiro entre
    promos), o "fuera_de_segmento". La transparencia del skip es deliberada:
    el operador ve a quién NO le llegó y por qué.
    """
    wanted = set(campaign.get("segments") or [])
    removed_ids = set(campaign.get("excluded_session_ids") or [])
    extra_ids = set(campaign.get("extra_session_ids") or [])
    recipients: list[CampaignRecipient] = []
    skipped: list[SkippedRecipient] = []
    for session_id, metadata in sessions:
        segment = segment_for_metadata(metadata)
        if segment is None:
            # Humano / opt-out: absoluto — ni el agregado manual lo pisa.
            skipped.append(SkippedRecipient(session_id, "excluido"))
            continue
        if session_id in removed_ids:
            skipped.append(SkippedRecipient(session_id, "quitado_por_operador"))
            continue
        is_manual = session_id in extra_ids
        if not is_manual and segment not in wanted:
            skipped.append(SkippedRecipient(session_id, "fuera_de_segmento"))
            continue
        if is_quiet_hours is not None and is_quiet_hours(session_id):
            # Quiet hours protege SIEMPRE (es la hora del cliente, no un filtro).
            skipped.append(SkippedRecipient(session_id, "quiet_hours"))
            continue
        if (
            not is_manual
            and now_ms is not None
            and _has_recent_campaign_touch(metadata, now_ms)
        ):
            # El cooldown de 48h sí lo puede saltar un agregado manual
            # (decisión consciente del operador sobre UN contacto).
            skipped.append(SkippedRecipient(session_id, "campana_reciente"))
            continue
        recipients.append(
            CampaignRecipient(
                session_id=session_id,
                segment=SEGMENT_MANUAL
                if is_manual and segment not in wanted
                else segment,
                customer_name=customer_name_from_metadata(metadata),
            )
        )
    return CampaignAudience(recipients=recipients, skipped=skipped)


# El customer de ventas WhatsApp se crea en Medusa con nombre placeholder
# ("Cliente WhatsApp") — mismo criterio que el agente ETA: mejor saludar sin
# nombre que con uno falso.
_PLACEHOLDER_NAMES = {"cliente whatsapp", "cliente sin nombre", "cliente"}


def customer_name_from_metadata(metadata: dict[str, Any]) -> str | None:
    """Primer nombre del cliente si existe uno real, None si no."""
    candidates = (
        (metadata.get("registered_order") or {}).get("customer_name"),
        (metadata.get("profile") or {}).get("name"),
    )
    for full_name in candidates:
        if not isinstance(full_name, str) or not full_name.strip():
            continue
        if full_name.strip().lower() in _PLACEHOLDER_NAMES:
            continue
        return full_name.strip().split()[0]
    return None


@dataclass(frozen=True)
class RecipientSendPlan:
    """Un envío concreto: sesión + variables ya resueltas (R-JSON)."""

    session_id: str
    variables: dict[str, str]


@dataclass(frozen=True)
class CampaignSendPlan:
    """El plan completo que el workflow ejecuta — costo EXPLÍCITO pre-envío."""

    campaign_id: str
    template_name: str
    recipients: list[RecipientSendPlan]
    skipped: list[SkippedRecipient]
    unit_cost_usd_micros: int
    total_cost_usd_micros: int


def build_send_plan(
    campaign: dict[str, Any],
    sessions: list[tuple[str, dict[str, Any]]],
    rate_card: Any,
    *,
    now_ms: int | None = None,
    is_quiet_hours: Any = None,
) -> CampaignSendPlan:
    """Audiencia + variables por destinatario + costo estimado, puro."""
    audience = resolve_campaign_audience(
        campaign, sessions, now_ms=now_ms, is_quiet_hours=is_quiet_hours
    )
    names = {
        r.session_id: r.customer_name for r in audience.recipients
    }
    recipients = [
        RecipientSendPlan(
            session_id=r.session_id,
            variables=campaign_template_variables(
                campaign, customer_name=names[r.session_id]
            ),
        )
        for r in audience.recipients
    ]
    estimate = estimate_send_cost(
        recipient_count=len(recipients), rate_card=rate_card
    )
    return CampaignSendPlan(
        campaign_id=campaign["id"],
        template_name=campaign.get("template_name") or CAMPAIGN_TEMPLATE_NAME,
        recipients=recipients,
        skipped=audience.skipped,
        unit_cost_usd_micros=estimate.unit_cost_usd_micros,
        total_cost_usd_micros=estimate.total_usd_micros,
    )


def campaign_stats(
    campaign: dict[str, Any],
    sessions: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Métricas de atribución de UNA campaña sobre el vault (puro).

    `replied` = sesiones cuyo último inbound cae dentro de la ventana
    post-touch de ESTA campaña. `attributed_*` = episodios que arrancan en
    ventana y cerraron venta (mismo matcher de 7 días que usa el panel de
    Ads — `matching_campaign_touch` del read model de atribución).
    """
    from src.sdk.connectorkit import matching_campaign_touch

    campaign_id = campaign["id"]
    replied = 0
    orders = 0
    revenue_cop = 0
    for _session_id, metadata in sessions:
        touches = [
            t
            for t in (metadata.get("campaign_touches") or [])
            if isinstance(t, dict) and t.get("campaign_id") == campaign_id
        ]
        if not touches:
            continue
        if matching_campaign_touch(touches, metadata.get("last_inbound_at_ms")):
            replied += 1
        for episode in metadata.get("episodes") or []:
            if not isinstance(episode, dict):
                continue
            if matching_campaign_touch(touches, episode.get("started_at_ms")) is None:
                continue
            total = episode.get("order_total_cop")
            if isinstance(total, (int, float)) and not isinstance(total, bool):
                orders += 1
                revenue_cop += int(total)
    return {
        "replied": replied,
        "attributed_orders": orders,
        "attributed_revenue_cop": revenue_cop,
    }


def campaign_template_variables(
    campaign: dict[str, Any], *, customer_name: str | None
) -> dict[str, str]:
    """Variables del template aprobado para UN destinatario.

    Los nombres/orden matchean el spec `campaign_promo_marketing_v1` del
    catálogo. Ninguna variable puede quedar vacía (Meta rechaza params "").
    Los max_length del spec truncan acá (defensa: la UI ya limita antes).
    """
    name = (customer_name or "").strip()
    greeting = f"Hola {name}" if name else "Hola"

    message = campaign.get("message") or {}
    header = (message.get("header") or "").strip()
    body = (message.get("body") or "").strip()
    campaign_message = "\n\n".join(part for part in (header, body) if part)
    if not campaign_message:
        campaign_message = campaign.get("name") or "Tenemos una novedad para ti."

    campaign_offer = _campaign_offer_line(campaign)

    return {
        "greeting": greeting[:80],
        "campaign_message": campaign_message[:640],
        "campaign_offer": campaign_offer[:200],
    }


def _campaign_offer_line(campaign: dict[str, Any]) -> str:
    """Línea de oferta según objetivo — SIEMPRE non-empty."""
    coupon = (campaign.get("coupon_code") or "").strip()
    percent = campaign.get("percent") or 0
    valid_until = (campaign.get("valid_until") or "").strip()

    if coupon:
        line = f"Usa el código {coupon} al pagar"
        if valid_until:
            line += f" — válido hasta {valid_until}"
        return line + "."
    if percent:
        line = f"Aprovecha el {percent}% de descuento"
        if valid_until:
            line += f" — válido hasta {valid_until}"
        return line + ". Escríbeme aquí y te muestro el catálogo."
    return "Escríbeme aquí y te cuento más."


def new_campaign(
    *,
    campaign_id: str,
    name: str,
    now_ms: int,
    goal: str = "",
    percent: int = 0,
    coupon_code: str = "",
    valid_until: str = "",
    product_handle: str | None = None,
    segments: list[str] | None = None,
    header: str = "",
    body: str = "",
    footer: str = "",
    cta: str = "",
) -> dict[str, Any]:
    """Campaña nueva en estado draft — el shape canónico que persiste el store.

    Dict JSON-first (no dataclass): el boundary workflow↔activity solo cruza
    ids y DTOs chicos; el shape completo lo validan la API (pydantic) y el
    frontend (Zod).
    """
    return {
        "id": campaign_id,
        "name": name,
        "status": STATUS_DRAFT,
        "goal": goal,
        "percent": percent,
        "coupon_code": coupon_code,
        "valid_until": valid_until,
        "product_handle": product_handle,
        "segments": list(segments or []),
        # Curaduría manual de la audiencia (sección Ver audiencia):
        "excluded_session_ids": [],
        "extra_session_ids": [],
        "message": {"header": header, "body": body, "footer": footer, "cta": cta},
        "template_name": CAMPAIGN_TEMPLATE_NAME,
        "schedule_at_ms": None,
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
        "sent_at_ms": None,
        "send_result": None,
        "test_sends": [],
    }


@dataclass(frozen=True)
class SendCostEstimate:
    """Costo estimado PRE-envío de una campaña (USD micros — cost_unit_lesson:
    el pricing sub-cent jamás se modela en cents). La verdad autoritativa por
    mensaje la trae después el `pricing` del webhook de Meta."""

    recipient_count: int
    unit_cost_usd_micros: int
    total_usd_micros: int


def estimate_send_cost(*, recipient_count: int, rate_card: Any) -> SendCostEstimate:
    """Estimación del gasto de mandar la campaña a `recipient_count` contactos.

    Mismo criterio defensivo que la central de envío: categoría ausente o sin
    precio en el rate card ⇒ 0 (subcotiza; el webhook trae la verdad).
    """
    entry = rate_card.rates.get(CATEGORY_MARKETING)
    unit = (
        entry.usd_micros_per_message
        if entry is not None and entry.usd_micros_per_message is not None
        else 0
    )
    return SendCostEstimate(
        recipient_count=recipient_count,
        unit_cost_usd_micros=unit,
        total_usd_micros=unit * recipient_count,
    )
