"""Temporal activity para mandar eventos CAPI a Meta.

Vive separada de ``activities.py`` (que es el módulo de send-to-customer)
porque CAPI es atribución backend-only — no toca al cliente, no toca el
session_history. Manteniendo el bounded context limpio: ``activities.py``
mensajería, ``capi_activity.py`` atribución.

DEHA compliance:
  * R-DET: el workflow llama esta activity por nombre; aquí vive todo el I/O.
  * R-JSON: input son strings/simple primitives; output es ``CapiEventResult``
    (frozen dataclass JSON-serializable).
  * R-STATELESS: ningún cache global; lee config + metadata cada call.
  * R-HEARTBEAT: HTTP POST típico <2s, timeout 15s — bajo el threshold de
    10s, no necesita heartbeat. (Si futuro retry policy hace múltiples
    attempts, el orchestration timeout cubre.)
  * R-DIP: no importa workflows ni siblings; solo platform/whatsapp/* y
    platform/config.

Activity ID: ``send_capi_event_activity`` — name preservado en el decorator
para no romper history de workflows en vuelo.

Flujo del activity:
  1. Normalizar legacy "Lead"->"LeadSubmitted" + validar (LeadSubmitted | Purchase).
  2. Validar config (DATASET_ID + ACCESS_TOKEN + WABA_ID). Si falta → skip
     ``skipped_no_config`` (no levanta excepción — pre-launch siempre
     vacío, no queremos bloquear deploys).
  3. Leer ``metadata.json`` del session.
  4. Resolver ``ctwa_clid`` desde ``metadata["ctwa_referrals"][-1]`` (último
     CTWA touch). Si no hay → skip ``skipped_no_ctwa_clid``.
  5. Chequear attribution window (7 días desde el ``captured_at_ms`` del
     ctwa_clid). Si expiró → skip ``skipped_attribution_expired``.
  6. Chequear ``metadata["capi_terminal_event"] == "Purchase"`` — si ya
     se mandó Purchase, cualquier Lead posterior es ignorado por Meta y
     desperdicia HTTP call → skip ``skipped_terminal_event_reached``.
  7. Construir ``event_id`` estable (idempotencia Meta dedup):
       - Lead: ``lead_{session_id}_{episode_id}``
       - Purchase: ``purchase_{order_id}`` (del ``registered_order``).
  8. Chequear que el event_id no esté en ``metadata["capi_events_sent"][]``
     con status="sent" — si ya se mandó, skip ``skipped_already_sent``.
     Defensa contra reschedules del workflow (idempotencia local).
  9. Para Purchase: leer ``registered_order.total_cop`` + ``currency``.
     Si registered_order no existe / no success → skip
     ``skipped_no_registered_order``.
 10. POST a Meta Graph API ``v18.0/{dataset_id}/events``.
 11. Parse respuesta:
       - 200 + events_received >= 1 → ``sent``, persistir en metadata
       - 4xx → ``failed_4xx`` + raise ApplicationError(non_retryable=True)
       - 5xx / network → ``failed_5xx`` + raise (Temporal reintenta)
 12. Persistir resultado en ``metadata["capi_events_sent"][]`` y, si
     Purchase, settear ``metadata["capi_terminal_event"] = "Purchase"``.

Triggers (en workflow ``HubaraSalesSessionWorkflow``):
  * ``closing_tag == "COMPRA_EXITOSA"`` → schedule con ``event_name="Purchase"``
  * ``closing_tag in {"CONFIRMADO_PAGO_PENDIENTE", "CONFIRMADO_SIN_DATOS"}``
    → schedule con ``event_name="LeadSubmitted"``
  * Otros (RECHAZO / GHOSTED / TIMEOUT) → no schedule

Runbook humano operacional: ``.hubara/runbooks/meta_template_approval.md``
§11–§22.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any

import httpx
import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.platform.config import (
    META_CAPI_ACCESS_TOKEN,
    META_CAPI_DATASET_ID,
    META_CAPI_TEST_EVENT_CODE,
    WHATSAPP_BUSINESS_ACCOUNT_ID,
    WORKSPACE_VAULT_DIR,
)
from src.platform.whatsapp.capi import (
    LEAD_EVENT_NAME,
    LEGACY_LEAD_EVENT_NAME,
    META_CAPI_API_URL,
    CapiEvent,
    CapiEventResult,
    build_capi_request_body,
    build_lead_event,
    build_purchase_event,
    is_ctwa_clid_within_attribution_window,
    make_event_id_for_lead,
    make_event_id_for_purchase,
    validate_event_name,
)

log = structlog.get_logger()


# =============================================================================
# Constantes
# =============================================================================


#: Tags de cierre que el workflow puede mapear a Lead — intent cualificado
#: pero sin confirmación de pago todavía.
LEAD_CLOSING_TAGS: frozenset[str] = frozenset(
    {"CONFIRMADO_PAGO_PENDIENTE", "CONFIRMADO_SIN_DATOS"}
)

#: Tags de cierre que disparan Purchase — deal cerrado con pago confirmado.
PURCHASE_CLOSING_TAGS: frozenset[str] = frozenset({"COMPRA_EXITOSA"})

#: HTTP timeout para POST a Graph API. Meta responde típicamente en <2s.
_CAPI_HTTP_TIMEOUT_SECONDS: float = 15.0


# =============================================================================
# Helpers — metadata I/O (replican el patrón de `activities.py` para
# mantener simetría; podrían extraerse a `state.py` si crece más uso)
# =============================================================================


def _read_metadata(session_id: str) -> dict[str, Any]:
    """Lee metadata.json. Devuelve {} si no existe / corrupto."""
    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    if not metadata_file.exists():
        return {}
    try:
        return json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_metadata(session_id: str, data: dict[str, Any]) -> None:
    """Escribe metadata.json atómicamente (tmp + rename)."""
    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = metadata_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(metadata_file)


def _now_ms() -> int:
    return int(time.time() * 1000)


# =============================================================================
# Guard helpers (puros — no I/O, separables para test)
# =============================================================================


def _resolve_ctwa_attribution(
    metadata: dict[str, Any],
) -> tuple[str | None, int | None]:
    """Devuelve ``(ctwa_clid, captured_at_ms)`` del último CTWA touch del
    episode, o ``(None, None)`` si la sesión no vino de un CTWA ad.

    Usa ``metadata["ctwa_referrals"][-1]`` — el array es append-only
    poblado por ``IngestInboundMessage._handle_referral`` cuando llega un
    inbound con ``referral.ctwa_clid`` nuevo (gated por dedupe en
    ``ctwa_clids_seen``).
    """
    referrals = metadata.get("ctwa_referrals", [])
    if not isinstance(referrals, list) or not referrals:
        return None, None
    last = referrals[-1]
    if not isinstance(last, dict):
        return None, None
    ctwa_clid = last.get("ctwa_clid")
    captured_at_ms = last.get("captured_at_ms")
    if not isinstance(ctwa_clid, str) or not isinstance(captured_at_ms, int):
        return None, None
    return ctwa_clid, captured_at_ms


def _is_already_sent(metadata: dict[str, Any], event_id: str) -> bool:
    """True si ``event_id`` ya está en ``metadata["capi_events_sent"][]``
    con status="sent". Defensa contra schedules duplicados del workflow.
    """
    sent_list = metadata.get("capi_events_sent", [])
    if not isinstance(sent_list, list):
        return False
    for entry in sent_list:
        if (
            isinstance(entry, dict)
            and entry.get("event_id") == event_id
            and entry.get("status") == "sent"
        ):
            return True
    return False


def _resolve_purchase_payload(
    metadata: dict[str, Any],
) -> tuple[str | None, int | None, str | None]:
    """Devuelve ``(order_id, total_cop, currency)`` del último
    ``registered_order`` exitoso, o ``(None, None, None)`` si no hay.

    El campo ``metadata["registered_order"]`` lo escribe ``RegisterOrderTool``
    solo cuando el order register fue success (ver order_registration.py
    líneas 269-280).
    """
    order = metadata.get("registered_order")
    if not isinstance(order, dict):
        return None, None, None
    if not order.get("success"):
        return None, None, None
    order_id = order.get("order_id")
    total_cop = order.get("total_cop")
    currency = order.get("currency", "COP")
    if not isinstance(order_id, str) or not isinstance(total_cop, int):
        return None, None, None
    if not isinstance(currency, str):
        currency = "COP"
    return order_id, total_cop, currency


def _persist_capi_event_outcome(
    metadata: dict[str, Any],
    result: CapiEventResult,
) -> None:
    """Mutates ``metadata`` para registrar el outcome del CAPI send.

    Effects:
      * Append entry a ``metadata["capi_events_sent"][]``.
      * Si Purchase exitoso, settear ``metadata["capi_terminal_event"]``
        (lock contra Leads tardíos para el mismo ctwa_clid).
    """
    events = metadata.setdefault("capi_events_sent", [])
    if not isinstance(events, list):
        # defensivo: reset si shape inválida
        events = []
        metadata["capi_events_sent"] = events
    events.append(asdict(result))
    if result.status == "sent" and result.event_name == "Purchase":
        metadata["capi_terminal_event"] = "Purchase"


# =============================================================================
# HTTP layer
# =============================================================================


async def _post_capi_event(
    event: CapiEvent,
    dataset_id: str,
    access_token: str,
) -> tuple[int, dict[str, Any] | None, str | None]:
    """POST a Meta Graph API. Retorna ``(http_status, body_json,
    transport_error)``.

    ``transport_error`` es non-None solo si la request no llegó al server
    (DNS / connect timeout / read error). En ese caso ``http_status=0`` y
    ``body_json=None``.
    """
    url = META_CAPI_API_URL.format(dataset_id=dataset_id)
    body = build_capi_request_body(
        event, test_event_code=META_CAPI_TEST_EVENT_CODE or None
    )
    params = {"access_token": access_token}
    headers = {"Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=_CAPI_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url, params=params, headers=headers, json=body
            )
    except (httpx.HTTPError, httpx.NetworkError) as e:
        return 0, None, str(e)

    try:
        body_json = response.json()
    except (json.JSONDecodeError, ValueError):
        body_json = None
    return response.status_code, body_json, None


# =============================================================================
# Activity
# =============================================================================


@activity.defn(name="send_capi_event_activity")
async def send_capi_event_activity(
    session_id: str,
    episode_id: str,
    event_name: str,
) -> CapiEventResult:
    """Manda un evento Lead o Purchase a Meta CAPI para atribución CTWA.

    Args:
        session_id: ``wa_<phone>`` del chat — usado para leer metadata.
        episode_id: id del episodio (``ep_NNN``) — usado en event_id de Lead
            y para futura observability (no en el payload de Meta).
        event_name: ``"LeadSubmitted"`` o ``"Purchase"`` (acepta el\n            legacy ``"Lead"`` de workflows en vuelo y lo normaliza).

    Returns:
        ``CapiEventResult`` describiendo el outcome. ``status`` empieza con
        ``"sent"`` (ok), ``"skipped_..."`` (guard short-circuit) o
        ``"failed_..."`` (excepción raised después).

    Raises:
        ApplicationError(non_retryable=True) si Meta respondió 4xx — el
            problema es de payload/auth, retry no ayuda.
        ApplicationError (retryable) si 5xx o network error — Temporal
            reintenta según retry_policy del workflow.

    Side effects:
        Escribe ``metadata["capi_events_sent"][]`` con el outcome y, si
        Purchase exitoso, settea ``metadata["capi_terminal_event"]``.
    """
    # ----- 1. Normalize legacy + validate event_name (fail-fast) -----
    # Workflows en vuelo (pre-fix 2026-07-01) agendaron la activity con
    # "Lead"; Meta solo acepta "LeadSubmitted" para business_messaging
    # (error_subcode 2804066). Normalizamos en el boundary para no romper
    # replays ni re-schedules.
    if event_name == LEGACY_LEAD_EVENT_NAME:
        event_name = LEAD_EVENT_NAME
    validate_event_name(event_name)  # raises ValueError

    # ----- 2. Validate config (skip-not-raise: pre-launch tolerante) -----
    if not META_CAPI_DATASET_ID or not META_CAPI_ACCESS_TOKEN:
        log.info(
            "capi_skipped_no_config",
            session_id=session_id,
            event_name=event_name,
            has_dataset_id=bool(META_CAPI_DATASET_ID),
            has_access_token=bool(META_CAPI_ACCESS_TOKEN),
        )
        return CapiEventResult(
            status="skipped_no_config",
            event_id="",
            event_name=event_name,
        )
    if not WHATSAPP_BUSINESS_ACCOUNT_ID:
        log.warning(
            "capi_skipped_no_waba_id",
            session_id=session_id,
            event_name=event_name,
        )
        return CapiEventResult(
            status="skipped_no_waba_id",
            event_id="",
            event_name=event_name,
        )

    # ----- 3. Load metadata -----
    metadata = _read_metadata(session_id)
    if not metadata:
        log.warning(
            "capi_skipped_no_metadata", session_id=session_id, event_name=event_name
        )
        return CapiEventResult(
            status="skipped_no_metadata",
            event_id="",
            event_name=event_name,
        )

    # ----- 4. Resolve ctwa_clid (mandatory for attribution) -----
    ctwa_clid, ctwa_received_at_ms = _resolve_ctwa_attribution(metadata)
    if ctwa_clid is None or ctwa_received_at_ms is None:
        log.info(
            "capi_skipped_no_ctwa_clid",
            session_id=session_id,
            event_name=event_name,
        )
        return CapiEventResult(
            status="skipped_no_ctwa_clid",
            event_id="",
            event_name=event_name,
        )

    # ----- 5. Attribution window guard (7 days) -----
    now_ms = _now_ms()
    if not is_ctwa_clid_within_attribution_window(
        received_at_ms=ctwa_received_at_ms, now_ms=now_ms
    ):
        log.info(
            "capi_skipped_attribution_expired",
            session_id=session_id,
            event_name=event_name,
            received_at_ms=ctwa_received_at_ms,
            now_ms=now_ms,
            age_ms=now_ms - ctwa_received_at_ms,
        )
        return CapiEventResult(
            status="skipped_attribution_expired",
            event_id="",
            event_name=event_name,
        )

    # ----- 6. Terminal event guard (Purchase already won) -----
    if metadata.get("capi_terminal_event") == "Purchase":
        # Si ya mandamos Purchase para este ctwa_clid, cualquier Lead
        # posterior es ruido — Meta lo descarta por dedup y desperdiciamos
        # HTTP call. Purchase nuevo igual lo dejamos pasar (Meta dedupea
        # por event_id estable, así que la re-emisión es benigna).
        if event_name == LEAD_EVENT_NAME:
            log.info(
                "capi_skipped_terminal_event_reached",
                session_id=session_id,
                event_name=event_name,
            )
            return CapiEventResult(
                status="skipped_terminal_event_reached",
                event_id="",
                event_name=event_name,
            )

    # ----- 7. Build event_id + payload -----
    event: CapiEvent
    if event_name == LEAD_EVENT_NAME:
        event_id = make_event_id_for_lead(
            session_id=session_id, episode_id=episode_id
        )
        # Lead idempotency check antes de construir el event (saves alloc).
        if _is_already_sent(metadata, event_id):
            log.info(
                "capi_skipped_already_sent",
                session_id=session_id,
                event_id=event_id,
                event_name=event_name,
            )
            return CapiEventResult(
                status="skipped_already_sent",
                event_id=event_id,
                event_name=event_name,
            )
        event = build_lead_event(
            event_time=now_ms // 1000,  # Meta espera unix seconds
            event_id=event_id,
            waba_id=WHATSAPP_BUSINESS_ACCOUNT_ID,
            ctwa_clid=ctwa_clid,
        )
    else:  # Purchase
        order_id, total_cop, currency = _resolve_purchase_payload(metadata)
        if order_id is None or total_cop is None:
            log.warning(
                "capi_skipped_no_registered_order",
                session_id=session_id,
                event_name=event_name,
            )
            return CapiEventResult(
                status="skipped_no_registered_order",
                event_id="",
                event_name=event_name,
            )
        event_id = make_event_id_for_purchase(order_id=order_id)
        if _is_already_sent(metadata, event_id):
            log.info(
                "capi_skipped_already_sent",
                session_id=session_id,
                event_id=event_id,
                event_name=event_name,
            )
            # Igual settear terminal_event si por algún edge case quedó sin
            # settear (defensa). El persist no necesita HTTP.
            if metadata.get("capi_terminal_event") != "Purchase":
                metadata["capi_terminal_event"] = "Purchase"
                _write_metadata(session_id, metadata)
            return CapiEventResult(
                status="skipped_already_sent",
                event_id=event_id,
                event_name=event_name,
            )
        event = build_purchase_event(
            event_time=now_ms // 1000,
            event_id=event_id,
            waba_id=WHATSAPP_BUSINESS_ACCOUNT_ID,
            ctwa_clid=ctwa_clid,
            value=total_cop,
            currency=currency or "COP",
        )

    # ----- 8-11. POST + parse + retry policy -----
    http_status, body_json, transport_error = await _post_capi_event(
        event,
        dataset_id=META_CAPI_DATASET_ID,
        access_token=META_CAPI_ACCESS_TOKEN,
    )
    fbtrace_id: str | None = None
    if isinstance(body_json, dict):
        fbtrace_id = body_json.get("fbtrace_id")
        if not fbtrace_id:
            err = body_json.get("error")
            # Meta a veces devuelve `error` como dict (con fbtrace_id
            # adentro), a veces como string (mensajes de service-level).
            # Solo nested .get si es dict — defensa contra ambos shapes.
            if isinstance(err, dict):
                fbtrace_id = err.get("fbtrace_id")

    if transport_error is not None:
        # Network / DNS / connect — retryable. Temporal reintenta según
        # retry_policy del workflow caller.
        result = CapiEventResult(
            status="failed_5xx",
            event_id=event.event_id,
            event_name=event_name,
            http_status=None,
            error_detail=transport_error,
            fbtrace_id=None,
        )
        _persist_capi_event_outcome(metadata, result)
        _write_metadata(session_id, metadata)
        raise ApplicationError(
            f"CAPI POST transport error: {transport_error}",
            type="CapiTransportError",
        )

    if 200 <= http_status < 300:
        # Meta success — el body debería tener `events_received >= 1`.
        events_received = (
            int(body_json.get("events_received", 0)) if isinstance(body_json, dict) else 0
        )
        if events_received < 1:
            # Raro pero defensivo — 200 sin events_received es un signal
            # bug. Tratamos como failed_other (no retryable).
            result = CapiEventResult(
                status="failed_other",
                event_id=event.event_id,
                event_name=event_name,
                http_status=http_status,
                error_detail=f"200 OK but events_received={events_received}",
                fbtrace_id=fbtrace_id,
            )
            _persist_capi_event_outcome(metadata, result)
            _write_metadata(session_id, metadata)
            log.error(
                "capi_send_unexpected_response",
                session_id=session_id,
                event_id=event.event_id,
                http_status=http_status,
                body=body_json,
            )
            return result
        result = CapiEventResult(
            status="sent",
            event_id=event.event_id,
            event_name=event_name,
            http_status=http_status,
            error_detail=None,
            fbtrace_id=fbtrace_id,
        )
        _persist_capi_event_outcome(metadata, result)
        _write_metadata(session_id, metadata)
        log.info(
            "capi_send_success",
            session_id=session_id,
            event_id=event.event_id,
            event_name=event_name,
            ctwa_clid=ctwa_clid,
            fbtrace_id=fbtrace_id,
        )
        return result

    # ----- 4xx — non-retryable -----
    if 400 <= http_status < 500:
        error_detail = (
            json.dumps(body_json) if body_json is not None else "no_body"
        )
        result = CapiEventResult(
            status="failed_4xx",
            event_id=event.event_id,
            event_name=event_name,
            http_status=http_status,
            error_detail=error_detail,
            fbtrace_id=fbtrace_id,
        )
        _persist_capi_event_outcome(metadata, result)
        _write_metadata(session_id, metadata)
        log.error(
            "capi_send_failed_4xx",
            session_id=session_id,
            event_id=event.event_id,
            http_status=http_status,
            body=body_json,
            fbtrace_id=fbtrace_id,
        )
        raise ApplicationError(
            f"CAPI POST failed (non-retryable, status={http_status}): {error_detail}",
            non_retryable=True,
            type=f"CapiMetaError{http_status}",
        )

    # ----- 5xx — retryable -----
    error_detail = json.dumps(body_json) if body_json is not None else "no_body"
    result = CapiEventResult(
        status="failed_5xx",
        event_id=event.event_id,
        event_name=event_name,
        http_status=http_status,
        error_detail=error_detail,
        fbtrace_id=fbtrace_id,
    )
    _persist_capi_event_outcome(metadata, result)
    _write_metadata(session_id, metadata)
    log.warning(
        "capi_send_failed_5xx",
        session_id=session_id,
        event_id=event.event_id,
        http_status=http_status,
        body=body_json,
        fbtrace_id=fbtrace_id,
    )
    raise ApplicationError(
        f"CAPI POST failed (retryable, status={http_status}): {error_detail}",
        type="CapiServerError",
    )


__all__ = [
    "LEAD_CLOSING_TAGS",
    "PURCHASE_CLOSING_TAGS",
    "send_capi_event_activity",
]
