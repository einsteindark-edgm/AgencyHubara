"""Central de decisión de envío WhatsApp — la ÚNICA fuente de verdad de
SI / CÓMO / CON-QUÉ-COSTO mandar un outbound al cliente.

Objetivo (visión): todo subsistema que manda un mensaje consulta esta central
ANTES de invocar la activity de envío. Ver `WHATSAPP_WINDOW_STRATEGY.md` §6.

ESTADO REAL (2026-07-03, sé honesto — premortem A1):
  * `evaluate_send` YA está cableado en el path de gasto (`send_template_to_session`
    lo consulta y estampa `last_outbound_policy`). El test-gate
    `test_send_policy_chokepoint.py` verifica ese wiring (grep sobre el source —
    smoke, no un enforcement duro sobre TODOS los paths de envío).
  * `decide_reengagement` + `LeadState` son la **capa funnel LISTA pero SIN
    caller de producción todavía**. Su wiring en vivo (remarketing/watchdog o el
    agente GraphAgents autónomo) es la fase siguiente. NO afirmar "choke point
    único enforced" hasta que ese wiring exista.

`evaluate_send` encode la matriz de costo por envío (`WHATSAPP_WINDOW_STRATEGY.md`
§3), que cruza geometría de ventana (`window.py`) × category × rate card
(`cost.py`) → una `SendDecision` explicable.

Puro (R-DET): sin Temporal, sin `datetime.now()`, sin I/O. El caller pasa
`now_ms` desde una activity y el `RateCard` desde el composition root. Se consume
activity-side; la `SendDecision` puede volver al workflow (R-JSON).

Determinismo gotcha: NO `from __future__ import annotations` — `SendDecision`
puede cruzar el boundary workflow↔activity y Temporal DataConverter la
reconstruye vía `get_type_hints`; PEP 563 (string annotations) rompe eso dentro
del sandbox. Mismo criterio que `remarketing/watchdog_contracts.py`.
"""
from dataclasses import dataclass
from typing import Any

from src.platform.whatsapp.cost import RateCard
from src.platform.whatsapp.window import (
    is_in_ctwa_window,
    is_in_service_window,
)


# =============================================================================
# Vocabulario (canales + categorías)
# =============================================================================

#: El mensaje viaja como texto free-form (non-template). Solo permitido dentro
#: de la ventana de servicio 24h.
CHANNEL_FREE_FORM: str = "free_form"

#: El mensaje viaja como template aprobado. Permitido en cualquier momento.
CHANNEL_TEMPLATE: str = "template"

#: No se puede/no se debe mandar (ver `SendDecision.suppress_reason`).
CHANNEL_BLOCKED: str = "blocked"

CATEGORY_SERVICE: str = "service"
CATEGORY_UTILITY: str = "utility"
CATEGORY_MARKETING: str = "marketing"
CATEGORY_AUTHENTICATION: str = "authentication"


# =============================================================================
# DTO (R-JSON, frozen — cruza workflow↔activity)
# =============================================================================


@dataclass(frozen=True)
class SendDecision:
    """Veredicto de la central para UN outbound.

    * `allowed`               — False ⇒ no mandar (mirar `suppress_reason`).
    * `channel`               — free_form / template / blocked.
    * `recommended_category`  — service / utility / marketing / authentication.
    * `is_free`               — True ⇒ Meta NO cobra (dentro de 72h CTWA, o
                                service gratis pre-1-oct).
    * `expected_cost_micros`  — costo estimado PRE-envío en USD micros. La verdad
                                autoritativa la trae el `pricing` del webhook.
    * `rationale`             — por qué esta decisión (observabilidad).
    * `suppress_reason`       — si `allowed=False`, el motivo canónico.
    """

    allowed: bool
    channel: str
    recommended_category: str
    is_free: bool
    expected_cost_micros: int
    rationale: str
    suppress_reason: str | None = None


# =============================================================================
# API pública
# =============================================================================


def annotate_last_outbound_policy(
    metadata: dict[str, Any], decision: SendDecision
) -> None:
    """Estampa la estimación de la central en `metadata['last_outbound_policy']`.

    La info de costo/lane de CADA outbound sale de la central (choke point).
    JSON-serializable — se persiste con el outbound para el bucle de medición
    (lane free vs paga vs suprimido, `WHATSAPP_WINDOW_STRATEGY.md` §9).
    """
    metadata["last_outbound_policy"] = {
        "channel": decision.channel,
        "category": decision.recommended_category,
        "is_free": decision.is_free,
        "expected_cost_micros": decision.expected_cost_micros,
        "allowed": decision.allowed,
        "rationale": decision.rationale,
    }


def _rate_micros(rate_card: RateCard, category: str) -> int:
    """Rate de `category` en el card, o 0 si falta / está sin precio.

    Mismo criterio defensivo que `cost.compute_message_cost_micros` (category
    ausente o `usd_micros_per_message=None` ⇒ 0). El pre-check nunca revienta
    por un card incompleto — subcotiza a 0 (la verdad la trae el webhook).
    """
    entry = rate_card.rates.get(category)
    if entry is None or entry.usd_micros_per_message is None:
        return 0
    return entry.usd_micros_per_message


def evaluate_send(
    now_ms: int,
    metadata: dict[str, Any],
    channel: str,
    category: str,
    rate_card: RateCard,
) -> SendDecision:
    """Devuelve la `SendDecision` para mandar `channel`/`category` en `now_ms`.

    Encode la matriz de `WHATSAPP_WINDOW_STRATEGY.md` §3:

      * Dentro de 72h CTWA → TODO gratis (incluso marketing template).
      * free_form (service) → solo permitido dentro de la 24h CSW; su costo lo
        fija el rate card (`service`: 0 pre-1-oct, >0 post-1-oct).
      * template → permitido siempre; gratis solo dentro de 72h CTWA, si no
        cobra `rate_card[category]`.

    La ventana CTWA es ortogonal a la CSW: si CTWA está abierta el costo es 0
    aunque la CSW haya cerrado (pero free_form igual necesita la CSW abierta
    para poder mandarse).
    """
    in_ctwa = is_in_ctwa_window(now_ms, metadata)
    in_csw = is_in_service_window(now_ms, metadata)

    if channel == CHANNEL_FREE_FORM:
        if not in_csw:
            # No hay ventana de servicio abierta → free-form imposible. El
            # caller debe caer a un template (o suprimir).
            return SendDecision(
                allowed=False,
                channel=CHANNEL_BLOCKED,
                recommended_category=CATEGORY_SERVICE,
                is_free=False,
                expected_cost_micros=0,
                rationale="free_form bloqueado: ventana de servicio 24h cerrada",
                suppress_reason="csw_closed",
            )
        # Free-form siempre es category `service`, sin importar lo que pida el
        # caller.
        cost = 0 if in_ctwa else _rate_micros(rate_card, CATEGORY_SERVICE)
        is_free = cost == 0
        if in_ctwa:
            why = "free_form dentro de 72h CTWA (free entry point)"
        elif is_free:
            why = "free_form service gratis en CSW (rate 0, pre-1-oct)"
        else:
            why = "free_form service cobrado en CSW (post-1-oct)"
        return SendDecision(
            allowed=True,
            channel=CHANNEL_FREE_FORM,
            recommended_category=CATEGORY_SERVICE,
            is_free=is_free,
            expected_cost_micros=cost,
            rationale=why,
        )

    if channel == CHANNEL_TEMPLATE:
        cost = 0 if in_ctwa else _rate_micros(rate_card, category)
        is_free = cost == 0
        if in_ctwa:
            why = f"template {category} dentro de 72h CTWA (gratis, incl. marketing)"
        elif is_free:
            why = f"template {category} sin precio en el rate card → 0"
        else:
            why = f"template {category} cobrado (fuera de 72h CTWA)"
        return SendDecision(
            allowed=True,
            channel=CHANNEL_TEMPLATE,
            recommended_category=category,
            is_free=is_free,
            expected_cost_micros=cost,
            rationale=why,
        )

    # Canal desconocido → no mandar. Defensa: el caller pasó basura.
    return SendDecision(
        allowed=False,
        channel=CHANNEL_BLOCKED,
        recommended_category=category,
        is_free=False,
        expected_cost_micros=0,
        rationale=f"canal desconocido: {channel!r}",
        suppress_reason="unknown_channel",
    )


# =============================================================================
# Capa funnel — decisión de REACTIVACIÓN (Free-First Funnel)
# =============================================================================


TAG_HUMAN: str = "HUMANO"
TAG_CONVERTED: str = "COMPRA_EXITOSA"
TAG_PAYMENT_PENDING: str = "CONFIRMADO_PAGO_PENDIENTE"


@dataclass(frozen=True)
class LeadState:
    """Estado del lead relevante para decidir reactivación.

    `tag` es el tag de conversación (INTERESADO / COMPRA_EXITOSA /
    CONFIRMADO_PAGO_PENDIENTE / HUMANO / NO_ETIQUETADO). Los flags derivan de
    metadata del episodio (ver mapa de warmth en `WHATSAPP_WINDOW_STRATEGY.md`).

    `transactional_hook` = hay un motivo transaccional genuino para un utility
    (pedido a medias, pedido colocado, pago pendiente) — la ÚNICA justificación
    legítima de un utility template (promo en utility = recategorización a
    marketing, ver §5).
    """

    tag: str | None = None
    has_order_draft: bool = False
    has_registered_order: bool = False
    is_ctwa_lead: bool = False
    engaged: bool = False
    #: Opt-in explícito para gastar UN marketing pago (lead de alto valor). Off
    #: por default — el instinto conservador: no pagar si no se calentó en 72h.
    allow_paid_marketing: bool = False

    @property
    def transactional_hook(self) -> bool:
        return (
            self.has_order_draft
            or self.has_registered_order
            or self.tag == TAG_PAYMENT_PENDING
        )


def _suppress(reason: str, why: str) -> SendDecision:
    return SendDecision(
        allowed=False,
        channel=CHANNEL_BLOCKED,
        recommended_category=CATEGORY_MARKETING,
        is_free=False,
        expected_cost_micros=0,
        rationale=why,
        suppress_reason=reason,
    )


def decide_reengagement(
    now_ms: int,
    metadata: dict[str, Any],
    lead: LeadState,
    rate_card: RateCard,
) -> SendDecision:
    """Decide CÓMO (o si) reactivar un lead — el Free-First Funnel (§4).

    Precedencia:
      1. Estados terminales (HUMANO / COMPRA_EXITOSA) → suprimir. Ningún bot
         interviene si hay humano en el caso o ya convirtió.
      2. Fase A — CSW abierta (cliente activo) → free-form.
      3. Fase A — 72h CTWA abierta (CSW cerrada) → template GRATIS. Utility si
         hay gancho transaccional; si no, marketing (también gratis en 72h).
      4. Fase B — ambas cerradas:
         * gancho transaccional → utility barata (legítima).
         * `allow_paid_marketing` opt-in → UN marketing pago.
         * si no → suprimir (no pagar por un lead que no se calentó en 72h).

    Delega el cálculo de costo/allowed en `evaluate_send` (fuente única de la
    matriz) — esta capa solo elige canal + category según warmth × ventana.
    """
    # 1. Terminales.
    if lead.tag == TAG_HUMAN:
        return _suppress("human_owned", "caso con humano — ningún bot interviene")
    if lead.tag == TAG_CONVERTED:
        return _suppress("already_converted", "ya convirtió — no reactivar")

    in_ctwa = is_in_ctwa_window(now_ms, metadata)
    in_csw = is_in_service_window(now_ms, metadata)

    # 2. Fase A — cliente activo (CSW abierta) → free-form.
    if in_csw:
        return evaluate_send(
            now_ms, metadata, CHANNEL_FREE_FORM, CATEGORY_SERVICE, rate_card
        )

    # 3. Fase A — 72h CTWA abierta, CSW cerrada → template gratis.
    if in_ctwa:
        category = CATEGORY_UTILITY if lead.transactional_hook else CATEGORY_MARKETING
        return evaluate_send(
            now_ms, metadata, CHANNEL_TEMPLATE, category, rate_card
        )

    # 4. Fase B — ambas cerradas.
    if lead.transactional_hook:
        return evaluate_send(
            now_ms, metadata, CHANNEL_TEMPLATE, CATEGORY_UTILITY, rate_card
        )
    if lead.allow_paid_marketing:
        return evaluate_send(
            now_ms, metadata, CHANNEL_TEMPLATE, CATEGORY_MARKETING, rate_card
        )
    return _suppress(
        "fase_b_cold_suppressed",
        "fase B: lead frío fuera de ventanas, sin gancho transaccional — no pagar",
    )
