"""Activities Temporal de Meta CAPI — adaptadores finos sobre ``capi_outbox``.

Vive separada de ``activities.py`` (send-to-customer) porque CAPI es
atribución backend-only: no toca al cliente ni al session_history.

Dos activities:

* ``flush_capi_outbox_activity(session_id)`` — el camino NUEVO (auditoría
  2026-09-08). Envía todo lo que los productores encolaron en
  ``metadata["capi_outbox"]`` durante el turno (tools, flush de UI intents,
  cierres). El workflow de Sales la ejecuta después de cada turno; el
  watchdog y el plugin ``orders`` llaman al flusher desde sus propias
  activities. Nunca levanta por un evento individual: el outbox ya
  persistió el estado y el próximo flush reintenta lo transitorio.

* ``send_capi_event_activity(session_id, episode_id, event_name)`` — el camino
  HISTÓRICO (cierre de episodio: ``COMPRA_EXITOSA`` → Purchase,
  ``CONFIRMADO_*`` → LeadSubmitted). Se conserva con el mismo nombre y
  contrato para las histories de workflows en vuelo: encola el evento y
  flushea en el acto. Mantiene la semántica de retry de Temporal: 4xx →
  ``ApplicationError(non_retryable)``; 5xx / connect → ``ApplicationError``
  retryable (la entrada sigue pendiente en el outbox); timeout ambiguo →
  ``unknown`` sin raise (L-1: reenviar duplicaría — Meta no deduplica).

DEHA: R-DET (el workflow solo llama por nombre), R-JSON (inputs primitivos,
output dataclass frozen), R-STATELESS (config leída por call), R-HEARTBEAT
(POST < 2s típico, timeout 15s), R-DIP (solo platform/*).

Runbook humano: ``.hubara/runbooks/meta_template_approval.md`` §11–§22.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

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
    CapiEventResult,
    make_event_id,
    validate_event_name,
)
from src.platform.whatsapp.capi_outbox import (
    CapiConfig,
    CapiFlushSummary,
    _already_finalized,
    _metadata_path,
    _read_metadata,
    _record,
    _write_metadata,
    enqueue_capi_event,
    flush_capi_outbox,
    resolve_ctwa_attribution,
)

log = structlog.get_logger()


# =============================================================================
# Constantes — mapeo closing_tag → evento (lo consume el workflow de Sales)
# =============================================================================


#: Tags de cierre que mapean a LeadSubmitted — intent cualificado pero sin
#: confirmación de pago todavía.
LEAD_CLOSING_TAGS: frozenset[str] = frozenset(
    {"CONFIRMADO_PAGO_PENDIENTE", "CONFIRMADO_SIN_DATOS"}
)

#: Tags de cierre que disparan Purchase — deal cerrado con pago confirmado.
PURCHASE_CLOSING_TAGS: frozenset[str] = frozenset({"COMPRA_EXITOSA"})


def _config() -> CapiConfig:
    """Config desde los globals de ESTE módulo (los tests los parchean)."""
    return CapiConfig(
        dataset_id=META_CAPI_DATASET_ID,
        access_token=META_CAPI_ACCESS_TOKEN,
        waba_id=WHATSAPP_BUSINESS_ACCOUNT_ID,
        test_event_code=META_CAPI_TEST_EVENT_CODE,
        vault_dir=Path(WORKSPACE_VAULT_DIR),
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _resolve_purchase_payload(
    metadata: dict[str, Any],
) -> tuple[str | None, int | None, str | None]:
    """``(order_id, total_cop, currency)`` del ``registered_order`` exitoso
    (lo escribe ``RegisterOrderTool``), o ``(None, None, None)``."""
    order = metadata.get("registered_order")
    if not isinstance(order, dict) or not order.get("success"):
        return None, None, None
    order_id = order.get("order_id")
    total_cop = order.get("total_cop")
    currency = order.get("currency", "COP")
    if not isinstance(order_id, str) or not isinstance(total_cop, int):
        return None, None, None
    if not isinstance(currency, str):
        currency = "COP"
    return order_id, total_cop, currency


# =============================================================================
# Activity nueva: flush del outbox
# =============================================================================


@activity.defn(name="flush_capi_outbox_activity")
async def flush_capi_outbox_activity(session_id: str) -> CapiFlushSummary:
    """Envía lo pendiente del outbox CAPI de la sesión. Idempotente; barato
    cuando no hay nada (solo lee metadata.json)."""
    result = await flush_capi_outbox(session_id, config=_config())
    return result.summary()


# =============================================================================
# Activity histórica: un evento por cierre de episodio
# =============================================================================


@activity.defn(name="send_capi_event_activity")
async def send_capi_event_activity(
    session_id: str,
    episode_id: str,
    event_name: str,
) -> CapiEventResult:
    """Encola + flushea UN evento de cierre (LeadSubmitted / Purchase).

    Args:
        session_id: ``wa_<phone>`` — dueño del metadata.json.
        episode_id: ``ep_NNN`` — va en el event_id de LeadSubmitted.
        event_name: ``"LeadSubmitted"`` | ``"Purchase"`` (acepta el legacy
            ``"Lead"`` de workflows en vuelo y lo normaliza).
    """
    if event_name == LEGACY_LEAD_EVENT_NAME:
        event_name = LEAD_EVENT_NAME
    validate_event_name(event_name)

    cfg = _config()
    if not cfg.enabled:
        log.info(
            "capi_skipped_no_config",
            session_id=session_id,
            event_name=event_name,
            has_dataset_id=bool(cfg.dataset_id),
            has_access_token=bool(cfg.access_token),
        )
        return CapiEventResult(status="skipped_no_config", event_id="", event_name=event_name)
    if not cfg.waba_id:
        log.warning("capi_skipped_no_waba_id", session_id=session_id, event_name=event_name)
        return CapiEventResult(status="skipped_no_waba_id", event_id="", event_name=event_name)

    path = _metadata_path(cfg.vault_dir, session_id)
    metadata = _read_metadata(path)
    if not metadata:
        log.warning("capi_skipped_no_metadata", session_id=session_id, event_name=event_name)
        return CapiEventResult(status="skipped_no_metadata", event_id="", event_name=event_name)

    now_ms = _now_ms()
    clid, _captured = resolve_ctwa_attribution(metadata)
    if clid is None:
        # Sesión orgánica: no hay nada que atribuir. Se deja rastro (antes
        # este skip vivía solo en logs — hallazgo "Alto" de la auditoría).
        stub = {"event_id": "", "event_name": event_name, "source": "episode_close"}
        _record(metadata, stub, status="skipped_no_ctwa_clid", now_ms=now_ms)
        _write_metadata(path, metadata)
        log.info("capi_skipped_no_ctwa_clid", session_id=session_id, event_name=event_name)
        return CapiEventResult(status="skipped_no_ctwa_clid", event_id="", event_name=event_name)

    order_id: str | None = None
    value: int | None = None
    currency: str | None = None
    if event_name == "Purchase":
        order_id, value, currency = _resolve_purchase_payload(metadata)
        if order_id is None or value is None:
            stub = {"event_id": "", "event_name": event_name, "source": "episode_close"}
            _record(metadata, stub, status="skipped_no_registered_order", now_ms=now_ms)
            _write_metadata(path, metadata)
            log.warning("capi_skipped_no_registered_order", session_id=session_id, event_name=event_name)
            return CapiEventResult(status="skipped_no_registered_order", event_id="", event_name=event_name)

    event_id = make_event_id(event_name, session_id=session_id, episode_id=episode_id, order_id=order_id)
    if _already_finalized(metadata, event_id):
        if event_name == "Purchase" and metadata.get("capi_terminal_event") != "Purchase":
            metadata["capi_terminal_event"] = "Purchase"
            _write_metadata(path, metadata)
        log.info("capi_skipped_already_sent", session_id=session_id, event_id=event_id, event_name=event_name)
        return CapiEventResult(status="skipped_already_sent", event_id=event_id, event_name=event_name)

    enqueue_capi_event(
        metadata,
        event_name=event_name,
        session_id=session_id,
        episode_id=episode_id,
        order_id=order_id,
        value=value,
        currency=currency,
        source="episode_close",
        now_ms=now_ms,
    )
    _write_metadata(path, metadata)

    flushed = await flush_capi_outbox(session_id, config=cfg, now_ms=now_ms)
    outcome = flushed.outcome_for(event_id)
    if outcome is None:
        # Sigue pendiente en el outbox (5xx / connect): dejar que Temporal
        # reintente la activity; el outbox garantiza un solo envío.
        pending = next(
            (e for e in (_read_metadata(path) or {}).get("capi_outbox", []) if e.get("event_id") == event_id),
            None,
        )
        detail = (pending or {}).get("last_error") or "pending"
        raise ApplicationError(
            f"CAPI POST failed (retryable): {detail}",
            type="CapiServerError",
        )

    status = str(outcome.get("status"))
    result = CapiEventResult(
        status=status,
        event_id=event_id,
        event_name=event_name,
        http_status=outcome.get("http_status"),
        error_detail=outcome.get("error_detail"),
        fbtrace_id=outcome.get("fbtrace_id"),
    )
    if status == "failed_4xx":
        raise ApplicationError(
            f"CAPI POST failed (non-retryable, status={result.http_status}): {result.error_detail}",
            non_retryable=True,
            type=f"CapiMetaError{result.http_status}",
        )
    return result


__all__ = [
    "LEAD_CLOSING_TAGS",
    "PURCHASE_CLOSING_TAGS",
    "flush_capi_outbox_activity",
    "send_capi_event_activity",
]
