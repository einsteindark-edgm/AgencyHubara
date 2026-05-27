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
  * Only ``Lead`` and ``Purchase`` events are supported for
    ``action_source: business_messaging``. Anything else is silently ignored.
  * 1 CAPI event counts per ad click; the strongest event wins (Purchase >
    Lead). Once we've sent Purchase, sending Lead afterwards is wasted call.
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


# =============================================================================
# Constants
# =============================================================================


#: API endpoint template. Caller plugs ``dataset_id``.
META_CAPI_API_URL: str = "https://graph.facebook.com/v18.0/{dataset_id}/events"

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

#: Allowed event names for CAPI Business Messaging. Anything outside this set
#: is rejected at the builder layer to fail-fast instead of having Meta
#: silently ignore it.
ALLOWED_EVENT_NAMES: frozenset[str] = frozenset({"Lead", "Purchase"})


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

    event_name: str  # "Lead" | "Purchase"
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
    """Build a Lead event. No monetary data — Meta accepts it as a soft
    conversion signal."""
    return CapiEvent(
        event_name="Lead",
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
    "ALLOWED_EVENT_NAMES",
    # DTOs
    "CapiUserData",
    "CapiCustomData",
    "CapiEvent",
    "CapiEventResult",
    # Builders
    "build_lead_event",
    "build_purchase_event",
    "make_event_id_for_lead",
    "make_event_id_for_purchase",
    "build_capi_request_body",
    # Guards
    "is_ctwa_clid_within_attribution_window",
    "validate_event_name",
]
