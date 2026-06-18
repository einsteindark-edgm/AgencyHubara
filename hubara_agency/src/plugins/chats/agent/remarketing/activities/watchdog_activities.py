"""Activities for the ServiceWindowWatchdogWorkflow (HU-WA24H-001 Sprint 2).

Three activities, all <10s worst-case so no heartbeat needed (R-HEARTBEAT
non-applicable). All take session_id + simple args and read/write the
per-session `metadata.json` via the filesystem store.

Activities live under the remarketing agent because the watchdog is owned
by the remarketing worker (decision in §5.1: reuse `remarketing` worker
host vs new `watchdog` worker — overhead vs separation tradeoff favored
reuse). The send is intentionally MOCK while Sprint 0 (Meta template
approval) is in flight — when the real templates exist, swap to
`send_whatsapp_template_activity` from `platform/whatsapp/activities.py`.

No `from __future__ import annotations`: same reasoning as the dispatcher —
Temporal's DataConverter rebuilds the dataclass field types at the workflow
boundary via `get_type_hints`, which fails inside the sandbox for PEP 563
string annotations of locally-defined types like `WatchdogEligibilityResult`.
"""
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from temporalio import activity

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.constants import (
    ROUTE_REMARKETING,
    ROUTE_VENTAS,
    WHATSAPP_SESSION_PREFIX,
)
from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.dtos import OutboundResult
from src.platform.whatsapp.templates.registry import (
    TemplateSpec,
    get_watchdog_template_for_stage,
)
from src.platform.whatsapp.composition import get_template_registry
from src.platform.whatsapp.window import watchdog_pre_expiry_ms
from src.plugins.chats.agent.remarketing.watchdog_contracts import (
    WatchdogEligibilityResult,
)


# =============================================================================
# Quiet hours (HU-WA24H-001 pre-mortem F4.1)
# =============================================================================

#: Hora de inicio del horario permitido (hora local del cliente, 24h format).
#: Override via WATCHDOG_QUIET_HOURS_START env var.
_DEFAULT_QUIET_HOURS_START: int = 8

#: Hora de fin del horario permitido (exclusive).
#: Override via WATCHDOG_QUIET_HOURS_END env var.
_DEFAULT_QUIET_HOURS_END: int = 22

#: Mapping country code (sin +) → IANA timezone. Cubre los mercados objetivo
#: de AgencyHubara (LATAM core). Extensible: agregar entries cuando se
#: onboardee un mercado nuevo. Default fallback es UTC (conservador — si no
#: sabemos timezone, no presumimos hora local).
_COUNTRY_CODE_TO_TZ: dict[str, str] = {
    "57": "America/Bogota",          # Colombia
    "54": "America/Argentina/Buenos_Aires",  # Argentina
    "52": "America/Mexico_City",     # México
    "56": "America/Santiago",        # Chile
    "51": "America/Lima",            # Perú
    "55": "America/Sao_Paulo",       # Brasil
    "1": "America/New_York",         # USA / Canada (genérico — pueden tener varios TZ; conservador)
}


def _resolve_local_timezone(session_id: str) -> ZoneInfo:
    """Mapea el session_id (`wa_<+code><phone>`) a su IANA timezone.

    Heurística: el primer 1-3 dígitos del número de teléfono (post-`+`) son
    el country code. Hacemos longest-match contra `_COUNTRY_CODE_TO_TZ`.
    Si no matchea ningún prefijo conocido → UTC (no asumimos horario local
    erróneo).

    NOTA: country codes ambiguos (e.g. +1 cubre USA + Canada + varios
    pequeños) usan un TZ representativo. Para precisión sub-país se
    requeriría DI de un servicio de phone → timezone lookup (out of scope).
    """
    if not session_id.startswith(WHATSAPP_SESSION_PREFIX):
        return ZoneInfo("UTC")
    phone = session_id[len(WHATSAPP_SESSION_PREFIX):].lstrip("+")
    # Longest-match en country codes (1 dígito vs 2 vs 3).
    for code_len in (3, 2, 1):
        prefix = phone[:code_len]
        tz_name = _COUNTRY_CODE_TO_TZ.get(prefix)
        if tz_name:
            try:
                return ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                continue
    return ZoneInfo("UTC")


def _is_quiet_hours_for_session(session_id: str, now_utc: datetime) -> bool:
    """True si la hora LOCAL del cliente está fuera del horario permitido
    (08:00 - 22:00 default, override por env var)."""
    tz = _resolve_local_timezone(session_id)
    local_dt = now_utc.astimezone(tz)
    local_hour = local_dt.hour
    start = int(os.environ.get("WATCHDOG_QUIET_HOURS_START", _DEFAULT_QUIET_HOURS_START))
    end = int(os.environ.get("WATCHDOG_QUIET_HOURS_END", _DEFAULT_QUIET_HOURS_END))
    # Quiet hours: hora < start O hora >= end. Allowed: start <= hora < end.
    return not (start <= local_hour < end)


log = structlog.get_logger()


# =============================================================================
# Feature flag
# =============================================================================


_WATCHDOG_ENABLED_ENV_VAR: str = "WATCHDOG_ENABLED"


def _watchdog_enabled() -> bool:
    """Read the WATCHDOG_ENABLED env var as a boolean. Default False.

    Read at every activity invocation (NOT cached at import time) so flipping
    the env var in a running worker takes effect after the next activity
    execution — useful for emergency disable during a quality_rating incident
    without restarting the worker pod.
    """
    raw = os.environ.get(_WATCHDOG_ENABLED_ENV_VAR, "").lower()
    return raw in {"1", "true", "yes", "on"}


# =============================================================================
# Episode stage inference
# =============================================================================


def _infer_episode_stage(metadata: dict) -> str | None:
    """Best-effort mapping from `metadata.json` shape to a template stage key.

    The template catalog (`platform/whatsapp/templates/catalog.yaml`) declares
    `requires_episode_stage` per template (`awaiting_quote` / `awaiting_payment`
    / `post_purchase` / `any`). This function picks one stage from the current
    metadata so `get_watchdog_template_for_stage` can resolve a single
    template.

    Heuristics, in order of priority:
      1. `registered_order` present AND a closing tag of COMPRA_EXITOSA in the
         active episode → `post_purchase`.
      2. `registered_order` present (any other state) → `awaiting_payment`.
         The order was registered but pago hasn't been confirmed yet.
      3. Active episode + `tag == INTERESADO` → `awaiting_quote` (cliente
         dudó después de ver la cotización).
      4. Default fallback → `awaiting_quote` (most common watchdog scenario
         for unknown episodes — the customer engaged but no progress).

    Returns the stage string. Callers pass it to
    `get_watchdog_template_for_stage`.
    """
    episodes = metadata.get("episodes") or []
    active_ep = episodes[-1] if episodes else None
    has_order = bool(metadata.get("registered_order"))
    tag = metadata.get("tag")

    if has_order:
        if active_ep and active_ep.get("closing_tag") == "COMPRA_EXITOSA":
            return "post_purchase"
        return "awaiting_payment"

    if tag == "INTERESADO":
        return "awaiting_quote"

    return "awaiting_quote"


# =============================================================================
# Template variable resolution
# =============================================================================


def _resolve_template_variables(
    spec: TemplateSpec, metadata: dict
) -> dict[str, str]:
    """Fill the variable slots a TemplateSpec declares, from metadata fields.

    Conservative resolution: if a variable can't be filled, fall back to a
    safe placeholder. Better to send a slightly impersonal template than fail
    the workflow at fire time — the watchdog is best-effort.

    Sources per variable name (matched literally against `var.name`):
      * `customer_first_name` → `metadata.profile.name` or first inbound's
        sender name, falling back to "amigo/a".
      * `product_or_quote_label` → tag motivo, falling back to "tu consulta".
      * `order_reference` → `metadata.registered_order.order_id` or "tu pedido".
      * `amount_currency` → not derivable from current metadata shape; uses
        "el monto del pedido" placeholder.
      * `status_label` → "en proceso" placeholder.
      * `product_label` → tag motivo, falling back to "el producto".
      * `discount_label` → "una promo especial" placeholder.

    Any variable not in the heuristics gets the literal string "—".
    """
    profile = metadata.get("profile") or {}
    customer_name = (
        profile.get("name")
        or (metadata.get("origin") or {}).get("first_inbound_name")
        or "amigo/a"
    )
    motivo = (metadata.get("motivo") or "").strip() or "tu consulta"
    registered = metadata.get("registered_order") or {}
    order_id = registered.get("order_id") or "tu pedido"

    defaults: dict[str, str] = {
        "customer_first_name": customer_name,
        "product_or_quote_label": motivo,
        "order_reference": str(order_id),
        "amount_currency": "el monto del pedido",
        "status_label": "en proceso",
        "product_label": motivo,
        "discount_label": "una promo especial",
    }

    out: dict[str, str] = {}
    for var in spec.variables:
        raw = defaults.get(var.name, "—")
        # Respect max_length defensively — Meta rejects oversized template
        # vars (specific error codes per language pack).
        if var.max_length is not None and len(raw) > var.max_length:
            raw = raw[: var.max_length]
        out[var.name] = raw
    return out


# =============================================================================
# Eligibility check
# =============================================================================


@activity.defn(name="check_watchdog_eligibility_activity")
async def check_watchdog_eligibility_activity(
    session_id: str,
    episode_id: str,
) -> WatchdogEligibilityResult:
    """Decide whether the watchdog should fire RIGHT NOW.

    Called by the workflow AFTER the timer fires, as a defense-in-depth
    re-check (the workflow was scheduled hours ago; state may have moved).

    Skip reasons (`eligible=False, reason=...`):
      * `"feature_flag_off"` — WATCHDOG_ENABLED env var is not truthy.
      * `"active_route_humano"` — a human took over; bot must not interfere.
      * `"no_active_episode"` — episode_id was closed since scheduling.
      * `"episode_id_mismatch"` — the active episode is NOT the one this
        watchdog was scheduled for (newer episode opened).
      * `"window_not_expiring_soon"` — service window expiry is more than
        `WATCHDOG_PRE_EXPIRY_MS` away (defensive: the timer fired early or
        the inbound was processed between scheduling and now).
      * `"no_template_for_stage"` — no utility template matches the current
        stage; nothing safe to send.

    On `eligible=True`, the result includes the resolved template name and
    pre-filled variables — the workflow passes them directly to the send
    activity without re-resolving.
    """
    if not _watchdog_enabled():
        log.info(
            "watchdog_eligibility_skipped",
            session_id=session_id,
            episode_id=episode_id,
            reason="feature_flag_off",
        )
        return WatchdogEligibilityResult(
            eligible=False, reason="feature_flag_off"
        )

    store = FilesystemMetadataStore(Path(WORKSPACE_VAULT_DIR))
    metadata = store.read(session_id)

    # 1. Route check — a human conversation must NOT receive a bot template.
    active_route = metadata.get("active_route")
    if active_route not in (ROUTE_VENTAS, ROUTE_REMARKETING):
        log.info(
            "watchdog_eligibility_skipped",
            session_id=session_id,
            episode_id=episode_id,
            reason="active_route_humano",
            active_route=active_route,
        )
        return WatchdogEligibilityResult(
            eligible=False, reason="active_route_humano"
        )

    # 2. Episode lifecycle check — must still be on the same active episode.
    episodes = metadata.get("episodes") or []
    if not episodes:
        return WatchdogEligibilityResult(
            eligible=False, reason="no_active_episode"
        )
    active_ep = episodes[-1]
    if active_ep.get("closed_at_ms") is not None:
        return WatchdogEligibilityResult(
            eligible=False, reason="no_active_episode"
        )
    if active_ep.get("episode_id") != episode_id:
        return WatchdogEligibilityResult(
            eligible=False, reason="episode_id_mismatch"
        )

    # 3. Window check — only fire if window is within the configured lead
    #    (`watchdog_pre_expiry_ms()`, env WATCHDOG_PRE_EXPIRY_MINUTES) of
    #    closing. Mismo valor que usó `watchdog_fire_at` al programar el timer,
    #    así un lead más largo no se auto-skipea como "too early". Si un inbound
    #    fresco extendió la ventana (y el reschedule signal se perdió), NO
    #    nudge — el watchdog dispararía demasiado temprano.
    now_ms = _now_ms()
    expires_at = metadata.get("service_window_expires_at_ms")
    if not isinstance(expires_at, int):
        return WatchdogEligibilityResult(
            eligible=False, reason="window_not_expiring_soon"
        )
    ms_until_expiry = expires_at - now_ms
    if ms_until_expiry > watchdog_pre_expiry_ms():
        log.info(
            "watchdog_eligibility_skipped",
            session_id=session_id,
            episode_id=episode_id,
            reason="window_not_expiring_soon",
            ms_until_expiry=ms_until_expiry,
        )
        return WatchdogEligibilityResult(
            eligible=False, reason="window_not_expiring_soon"
        )

    # 4. Quiet hours check (HU-WA24H-001 pre-mortem F4.1) — no disparar en
    #    horario nocturno hora local del cliente (3am Colombia = quality
    #    rating drop garantizado).
    if _is_quiet_hours_for_session(session_id, datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))):
        log.info(
            "watchdog_eligibility_skipped",
            session_id=session_id,
            episode_id=episode_id,
            reason="outside_quiet_hours",
        )
        return WatchdogEligibilityResult(
            eligible=False, reason="outside_quiet_hours"
        )

    # 5. Resolve template for the current episode stage.
    stage = _infer_episode_stage(metadata)
    registry = get_template_registry()
    spec = get_watchdog_template_for_stage(registry, stage or "")
    if spec is None:
        log.info(
            "watchdog_eligibility_skipped",
            session_id=session_id,
            episode_id=episode_id,
            reason="no_template_for_stage",
            stage=stage,
        )
        return WatchdogEligibilityResult(
            eligible=False, reason="no_template_for_stage"
        )

    variables = _resolve_template_variables(spec, metadata)
    log.info(
        "watchdog_eligibility_pass",
        session_id=session_id,
        episode_id=episode_id,
        template=spec.name,
        stage=stage,
    )
    return WatchdogEligibilityResult(
        eligible=True,
        resolved_template_name=spec.name,
        resolved_template_variables=variables,
    )


# =============================================================================
# Send template (MOCK — Sprint 2)
# =============================================================================


@activity.defn(name="send_watchdog_template_activity")
async def send_watchdog_template_activity(
    session_id: str,
    template_name: str,
    variables: dict,
) -> OutboundResult:
    """MOCK send: log the would-be template send, NEVER call Meta.

    Sprint 2 deliberately mocks the real send while Sprint 0 (operational —
    Meta template approval, copywriter, runbook) is pending. The OutboundResult
    shape matches `send_whatsapp_template_activity` so the workflow code path
    is identical; swapping to the real activity is one-line change in
    `remarketing.py` (register the real activity instead of this one) +
    workflow `args=...` pointer.

    `wa_message_id` carries a `MOCK_WATCHDOG_<uuid>` prefix so downstream
    log inspection / dashboards can filter mock fires out of cost reporting
    without confusing them with real Meta-issued message ids.
    """
    mock_id = f"MOCK_WATCHDOG_{uuid.uuid4().hex[:12]}"
    log.info(
        "watchdog_template_send_MOCK",
        session_id=session_id,
        template_name=template_name,
        variables=variables,
        mock_wa_message_id=mock_id,
        note=(
            "Sprint 2 MOCK — not actually calling WhatsApp. Replace with "
            "send_whatsapp_template_activity once Sprint 0 template approval "
            "completes."
        ),
    )
    return OutboundResult(wa_message_id=mock_id, ok=True, error=None)


# =============================================================================
# Persist outcome
# =============================================================================


# Valid outcomes — mirror the lifecycle states the workflow can produce.
_VALID_OUTCOMES: frozenset[str] = frozenset(
    {"fired", "cancelled", "skipped", "failed"}
)


@activity.defn(name="persist_watchdog_outcome_activity")
async def persist_watchdog_outcome_activity(
    session_id: str,
    outcome: str,
    detail: str | None,
) -> None:
    """Write the watchdog block of metadata.json with the terminal outcome.

    The block lives at `metadata["watchdog"]` and follows the JSON shape
    declared in HU-WA24H-001 §3.1. Each outcome sets a specific field set:

      * `"fired"`     → `fired_at_ms = now`, `cancelled_at_ms = None`,
                        `reason_cancelled = None`. `detail` carries the
                        wa_message_id of the sent template (or the MOCK id).
      * `"cancelled"` → `cancelled_at_ms = now`, `reason_cancelled = detail`.
      * `"skipped"`   → `cancelled_at_ms = now`, `reason_cancelled =
                        f"skipped:{detail}"` (preserving the eligibility
                        skip reason).
      * `"failed"`    → `cancelled_at_ms = now`, `reason_cancelled =
                        f"failed:{detail}"`.

    Idempotent under retry — overwriting the watchdog block with the same
    outcome twice is a no-op. The activity does NOT touch any other
    metadata key.
    """
    if outcome not in _VALID_OUTCOMES:
        raise ValueError(
            f"Invalid watchdog outcome {outcome!r}. "
            f"Valid: {sorted(_VALID_OUTCOMES)}"
        )

    store = FilesystemMetadataStore(Path(WORKSPACE_VAULT_DIR))
    metadata = store.read(session_id)
    existing = dict(metadata.get("watchdog") or {})

    now_ms = _now_ms()
    if outcome == "fired":
        existing["fired_at_ms"] = now_ms
        existing["cancelled_at_ms"] = None
        existing["reason_cancelled"] = None
        # HU-WA24H-001 pre-mortem F5.2: persistir el wa_message_id para que
        # el dashboard del operador correlacione metadata.watchdog ↔ JSONL del
        # session_history sin tener que cruzar logs estructurados.
        existing["last_fire_wa_message_id"] = detail
        log.info(
            "watchdog_outcome_persisted",
            session_id=session_id,
            outcome=outcome,
            wa_message_id=detail,
        )
    elif outcome == "cancelled":
        existing["cancelled_at_ms"] = now_ms
        existing["reason_cancelled"] = detail
        log.info(
            "watchdog_outcome_persisted",
            session_id=session_id,
            outcome=outcome,
            reason=detail,
        )
    elif outcome == "skipped":
        existing["cancelled_at_ms"] = now_ms
        existing["reason_cancelled"] = f"skipped:{detail}" if detail else "skipped"
        log.info(
            "watchdog_outcome_persisted",
            session_id=session_id,
            outcome=outcome,
            reason=detail,
        )
    else:  # "failed"
        existing["cancelled_at_ms"] = now_ms
        existing["reason_cancelled"] = f"failed:{detail}" if detail else "failed"
        log.info(
            "watchdog_outcome_persisted",
            session_id=session_id,
            outcome=outcome,
            reason=detail,
        )

    metadata["watchdog"] = existing
    store.write(session_id, metadata)


# =============================================================================
# Internal helpers
# =============================================================================


def _now_ms() -> int:
    return int(time.time() * 1000)
