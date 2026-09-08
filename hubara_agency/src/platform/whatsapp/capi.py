"""Meta Conversions API (CAPI) for Business Messaging — DTOs + pure builder.

CAPI lets us tell Meta server-to-server that a CTWA-attributed conversation
converted into a Lead or a Purchase. Without CAPI, Meta's ad algorithm cannot
optimize for downstream WhatsApp sales — it only sees the click, not the
outcome. This module is the pure layer: DTOs, payload builder, validation.
HTTP and Temporal live in ``activities.py``.

References:
  * Runbook humano: ``.hubara/runbooks/meta_template_approval.md`` §11–§22.
  * Architecture memory: ``~/.claude/.../memory/capi_integration_plan.md``.
  * Meta docs:
    https://developers.facebook.com/docs/marketing-api/conversions-api/business-messaging/

Hard rules from Meta (encoded as constants below):
  * 14 event names are accepted for ``action_source: business_messaging``
    (see ``CAPI_EVENT_NAMES``). ``Lead`` (el nombre del CAPI web clásico)
    es RECHAZADO con error_subcode 2804066 — el nombre válido es
    ``LeadSubmitted``. Anything else is rejected or silently ignored.
  * Meta does NOT deduplicate business-messaging events (doc oficial):
    la idempotencia es NUESTRA — event_id estable + outbox local
    (``capi_outbox.py``). The strongest event wins in reporting (Purchase >
    Lead); once we've sent Purchase, pre-purchase events are skipped.
  * ``ctwa_clid`` is the attribution key. Without it, CAPI does nothing — the
    event lands but matches no ad impression. Always required.
  * 7-day attribution window from the ad click. Past that, Meta drops the
    event silently. We persist ``ctwa_clid_expires_at_ms`` in metadata.json
    to short-circuit at the activity level.

Unit conventions:
  * ``value`` field on Purchase: integer in the **currency unit** (e.g. COP
    cents — but in Colombia we use whole pesos because COP doesn't divide).
    Meta expects a numeric value; we serialize it as int to avoid float
    rounding surprises.
  * ``currency``: ISO 4217 — for Colombia always ``"COP"``. Hard-coded
    constant ``DEFAULT_CURRENCY`` so callers can't accidentally pass USD and
    blow up cost-per-purchase by 4000x.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.platform.meta.graph import graph_url


# =============================================================================
# Constants
# =============================================================================


#: API endpoint template. Caller plugs ``dataset_id``.
META_CAPI_API_URL: str = graph_url("{dataset_id}", "events")

#: Attribution window. Past this, the event lands but Meta won't match it.
#: 7 days in milliseconds.
CTWA_ATTRIBUTION_WINDOW_MS: int = 7 * 24 * 60 * 60 * 1000

#: Action source — locked to business_messaging for WhatsApp.
ACTION_SOURCE: str = "business_messaging"

#: Messaging channel — locked to WhatsApp.
MESSAGING_CHANNEL: str = "whatsapp"

#: Currency default for Colombia. Hard-coded to prevent USD-by-mistake (which
#: would multiply revenue reports by ~4000x and break Ads Manager cost-per-
#: purchase).
DEFAULT_CURRENCY: str = "COP"

#: Nombre del evento de lead para business_messaging. OJO: NO es "Lead" —
#: Meta lo rechaza con error_subcode 2804066 ("provide a valid value such as
#: 'Purchase' or 'LeadSubmitted'"). Bug real cazado por smoke test 2026-07-01
#: contra el dataset vivo; los ~40 unit tests eran verdes con "Lead" (gotcha
#: #1: schema permite ≠ Meta acepta).
LEAD_EVENT_NAME: str = "LeadSubmitted"

#: Nombre legacy que usábamos pre-fix. Workflows en vuelo pueden re-agendar
#: la activity con este valor — la activity lo normaliza en el boundary.
LEGACY_LEAD_EVENT_NAME: str = "Lead"

#: Los 14 eventos que Meta acepta para ``action_source: business_messaging``
#: (guía de onboarding "Conversions API for Business Messaging", 2026).
#: Solo ``Purchase`` y los leads desbloquean optimización de campañas; el
#: resto enriquece el reporte de embudo en Ads Manager. Cualquier otro nombre
#: se rechaza acá para fallar rápido en vez de que Meta lo ignore en silencio.
CAPI_EVENT_NAMES: frozenset[str] = frozenset(
    {
        "Purchase",
        LEAD_EVENT_NAME,
        "InitiateCheckout",
        "AddToCart",
        "ViewContent",
        "OrderCreated",
        "OrderShipped",
        "OrderDelivered",
        "OrderCanceled",
        "OrderReturned",
        "CartAbandoned",
        "QualifiedLead",
        "RatingProvided",
        "ReviewProvided",
    }
)

#: Alias histórico (pre-auditoría 2026-09-08 eran solo 2 eventos).
ALLOWED_EVENT_NAMES: frozenset[str] = CAPI_EVENT_NAMES

#: Eventos "antes de la compra". Una vez que mandamos ``Purchase`` para un
#: clic, estos ya no aportan nada (Meta cuenta el evento más fuerte) y se
#: saltan con ``skipped_terminal_event_reached``. Los post-compra
#: (OrderShipped / OrderDelivered / OrderCanceled / OrderReturned / Rating /
#: Review) sí se mandan después del Purchase.
PRE_PURCHASE_EVENT_NAMES: frozenset[str] = frozenset(
    {
        LEAD_EVENT_NAME,
        "QualifiedLead",
        "ViewContent",
        "AddToCart",
        "InitiateCheckout",
        "OrderCreated",
        "CartAbandoned",
    }
)

#: Eventos cuyo sujeto es un PEDIDO (event_id por ``order_id``); el resto se
#: identifica por episodio (``session_id`` + ``episode_id``).
ORDER_SCOPED_EVENT_NAMES: frozenset[str] = frozenset(
    {
        "Purchase",
        "OrderCreated",
        "OrderShipped",
        "OrderDelivered",
        "OrderCanceled",
        "OrderReturned",
    }
)


# =============================================================================
# DTOs (R-JSON, frozen)
# =============================================================================


@dataclass(frozen=True)
class CapiUserData:
    """Identifying fields Meta uses to match the event back to an ad click.

    For CTWA Business Messaging, the *only* required match key is
    ``ctwa_clid`` (paired with the WABA id for tenant scoping). Other
    standard CAPI keys (email, phone hashed SHA-256) are accepted by the
    API but not needed when ctwa_clid is present — and we don't have
    customer email yet at this stage.
    """

    whatsapp_business_account_id: str
    ctwa_clid: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "whatsapp_business_account_id": self.whatsapp_business_account_id,
            "ctwa_clid": self.ctwa_clid,
        }


@dataclass(frozen=True)
class CapiCustomData:
    """Event-specific payload. Required for Purchase, optional for Lead.

    For Purchase: ``value`` is the order total, ``currency`` is ISO 4217.
    For Lead: leave both as None — Meta accepts the event without monetary
    context for lead conversions.
    """

    value: int | None = None
    currency: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.value is not None:
            out["value"] = self.value
        if self.currency is not None:
            out["currency"] = self.currency
        return out


@dataclass(frozen=True)
class CapiEvent:
    """A single CAPI event payload, ready to wrap in the ``{"data": [...]}``
    envelope at HTTP time.

    The ``event_id`` is stable across retries (built from session/order ids)
    so Meta dedupes correctly — never use UUIDs random or the same logical
    event lands twice if Temporal retries.
    """

    event_name: str  # "LeadSubmitted" | "Purchase"
    event_time: int  # unix seconds (NOT millis — Meta uses seconds here)
    event_id: str
    user_data: CapiUserData
    custom_data: CapiCustomData = field(default_factory=CapiCustomData)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_name": self.event_name,
            "event_time": self.event_time,
            "event_id": self.event_id,
            "action_source": ACTION_SOURCE,
            "messaging_channel": MESSAGING_CHANNEL,
            "user_data": self.user_data.to_dict(),
        }
        custom = self.custom_data.to_dict()
        if custom:
            payload["custom_data"] = custom
        return payload


@dataclass(frozen=True)
class CapiEventResult:
    """Outcome of a CAPI send. Surfaced back to the workflow / metadata.

    ``status``:
      * ``"sent"``           — Meta accepted (HTTP 200 + events_received >= 1).
      * ``"skipped_<reason>"`` — short-circuit by guard; nothing sent.
      * ``"failed_4xx"``     — Meta rejected (auth / malformed); non-retryable.
      * ``"failed_5xx"``     — server error or network; retryable by Temporal.
      * ``"failed_other"``   — unexpected (parse errors, etc.).
    """

    status: str
    event_id: str
    event_name: str
    http_status: int | None = None
    error_detail: str | None = None
    fbtrace_id: str | None = None  # Meta debug id — paste in support tickets


# =============================================================================
# Builders (pure)
# =============================================================================


def build_lead_event(
    *,
    event_time: int,
    event_id: str,
    waba_id: str,
    ctwa_clid: str,
) -> CapiEvent:
    """Build a LeadSubmitted event. No monetary data — Meta accepts it as a
    soft conversion signal."""
    return CapiEvent(
        event_name=LEAD_EVENT_NAME,
        event_time=event_time,
        event_id=event_id,
        user_data=CapiUserData(
            whatsapp_business_account_id=waba_id,
            ctwa_clid=ctwa_clid,
        ),
        custom_data=CapiCustomData(),
    )


def build_purchase_event(
    *,
    event_time: int,
    event_id: str,
    waba_id: str,
    ctwa_clid: str,
    value: int,
    currency: str = DEFAULT_CURRENCY,
) -> CapiEvent:
    """Build a Purchase event with monetary data.

    Hard validation: ``value`` must be a positive int. ``currency`` is ISO
    4217 — caller should always pass ``"COP"`` for Colombia. We don't reject
    other currencies (the API supports multi-currency) but the
    ``DEFAULT_CURRENCY`` constant nudges callers to the right answer.
    """
    if not isinstance(value, int) or value <= 0:
        raise ValueError(
            f"Purchase value must be positive int, got {value!r} ({type(value).__name__})"
        )
    if not currency or len(currency) != 3:
        raise ValueError(
            f"Purchase currency must be 3-letter ISO 4217 code, got {currency!r}"
        )
    return CapiEvent(
        event_name="Purchase",
        event_time=event_time,
        event_id=event_id,
        user_data=CapiUserData(
            whatsapp_business_account_id=waba_id,
            ctwa_clid=ctwa_clid,
        ),
        custom_data=CapiCustomData(value=value, currency=currency),
    )


def build_capi_event(
    *,
    event_name: str,
    event_time: int,
    event_id: str,
    waba_id: str,
    ctwa_clid: str,
    value: int | None = None,
    currency: str | None = None,
) -> CapiEvent:
    """Builder genérico para cualquiera de los 14 eventos.

    ``value``/``currency`` son opcionales salvo para ``Purchase`` (Meta los
    exige para calcular valor de compra y ROAS) — ahí delega en
    :func:`build_purchase_event`, que valida. Para el resto, si viene
    ``value`` sin ``currency`` se asume ``DEFAULT_CURRENCY``.
    """
    validate_event_name(event_name)
    if event_name == "Purchase":
        if value is None:
            raise ValueError("Purchase requiere value (total del pedido)")
        return build_purchase_event(
            event_time=event_time,
            event_id=event_id,
            waba_id=waba_id,
            ctwa_clid=ctwa_clid,
            value=value,
            currency=currency or DEFAULT_CURRENCY,
        )
    custom = CapiCustomData()
    if value is not None:
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"value debe ser int >= 0, got {value!r}")
        custom = CapiCustomData(value=value, currency=currency or DEFAULT_CURRENCY)
    return CapiEvent(
        event_name=event_name,
        event_time=event_time,
        event_id=event_id,
        user_data=CapiUserData(
            whatsapp_business_account_id=waba_id,
            ctwa_clid=ctwa_clid,
        ),
        custom_data=custom,
    )


def make_event_id(
    event_name: str,
    *,
    session_id: str,
    episode_id: str | None = None,
    order_id: str | None = None,
) -> str:
    """``event_id`` estable para cualquier evento — la idempotencia LOCAL
    depende de esto (Meta NO deduplica eventos de business messaging).

    * Eventos de pedido (``ORDER_SCOPED_EVENT_NAMES``): ``<evento>_<order_id>``.
      ``Purchase`` conserva el id histórico ``purchase_<order_id>``.
    * Eventos de episodio: ``<evento>_<session_id>_<episode_id>``.
      ``LeadSubmitted`` conserva ``lead_<session_id>_<episode_id>``.
    """
    validate_event_name(event_name)
    if event_name in ORDER_SCOPED_EVENT_NAMES:
        if not order_id:
            raise ValueError(f"{event_name} requiere order_id para su event_id")
        if event_name == "Purchase":
            return make_event_id_for_purchase(order_id=order_id)
        return f"{event_name.lower()}_{order_id}"
    if not episode_id:
        raise ValueError(f"{event_name} requiere episode_id para su event_id")
    if event_name == LEAD_EVENT_NAME:
        return make_event_id_for_lead(session_id=session_id, episode_id=episode_id)
    return f"{event_name.lower()}_{session_id}_{episode_id}"


def make_event_id_for_lead(*, session_id: str, episode_id: str) -> str:
    """Stable event_id for Lead — one Lead per (session, episode). If the
    same episode triggers Lead twice (e.g. retry), Meta dedupes."""
    return f"lead_{session_id}_{episode_id}"


def make_event_id_for_purchase(*, order_id: str) -> str:
    """Stable event_id for Purchase — one Purchase per order. Order ids are
    globally unique so this scopes correctly without needing session_id."""
    return f"purchase_{order_id}"


def build_capi_request_body(
    event: CapiEvent,
    *,
    test_event_code: str | None = None,
) -> dict[str, Any]:
    """Wrap a single event in the ``{"data": [...]}`` envelope.

    ``test_event_code`` (optional): when set, the event lands in the Test
    Events panel of Events Manager for debugging — does NOT show up in
    production attribution. Always None in prod; set in staging via env var.
    Goes at the ROOT of the envelope, NOT inside ``data[]`` — common
    mistake.
    """
    body: dict[str, Any] = {"data": [event.to_dict()]}
    if test_event_code:
        body["test_event_code"] = test_event_code
    return body


# =============================================================================
# Guards (pure — no I/O)
# =============================================================================


def is_ctwa_clid_within_attribution_window(
    *,
    received_at_ms: int,
    now_ms: int,
) -> bool:
    """Return True iff the ad click is still within Meta's 7-day attribution
    window. Past that, events land but Meta drops them silently.

    Comparison is strict ``<`` to give a tiny safety margin against clock
    skew at the boundary — we'd rather skip a borderline case than waste an
    HTTP call.
    """
    return (now_ms - received_at_ms) < CTWA_ATTRIBUTION_WINDOW_MS


def validate_event_name(event_name: str) -> None:
    """Raise ValueError if event_name isn't in ALLOWED_EVENT_NAMES. Fail-fast
    is better than the silent-ignore Meta does."""
    if event_name not in ALLOWED_EVENT_NAMES:
        raise ValueError(
            f"Event name {event_name!r} not supported for CAPI Business "
            f"Messaging. Allowed: {sorted(ALLOWED_EVENT_NAMES)}"
        )


__all__ = [
    # Constants
    "META_CAPI_API_URL",
    "CTWA_ATTRIBUTION_WINDOW_MS",
    "ACTION_SOURCE",
    "MESSAGING_CHANNEL",
    "DEFAULT_CURRENCY",
    "LEAD_EVENT_NAME",
    "LEGACY_LEAD_EVENT_NAME",
    "ALLOWED_EVENT_NAMES",
    "CAPI_EVENT_NAMES",
    "PRE_PURCHASE_EVENT_NAMES",
    "ORDER_SCOPED_EVENT_NAMES",
    # DTOs
    "CapiUserData",
    "CapiCustomData",
    "CapiEvent",
    "CapiEventResult",
    # Builders
    "build_lead_event",
    "build_purchase_event",
    "build_capi_event",
    "make_event_id",
    "make_event_id_for_lead",
    "make_event_id_for_purchase",
    "build_capi_request_body",
    # Guards
    "is_ctwa_clid_within_attribution_window",
    "validate_event_name",
]
