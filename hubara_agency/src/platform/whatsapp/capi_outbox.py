"""Outbox CAPI — el ÚNICO camino por el que un evento llega a Meta.

Auditoría 2026-09-08: el ``Purchase`` nunca salía (la confirmación humana
del pago no llamaba CAPI), los skips no dejaban rastro y el código asumía
que Meta deduplicaba por ``event_id`` (no lo hace para business messaging).
Este módulo resuelve los tres con un patrón outbox:

  1. **Cualquier productor encola** en ``metadata["capi_outbox"]`` con
     :func:`enqueue_capi_event` (puro: muta el dict que el caller ya tiene
     abierto). Productores: tools del turno de Sales, ``flush_ui_intents``,
     el cierre lazy por TIMEOUT, el watchdog de remarketing, la confirmación
     humana de pago / cancelación / etapas del plugin ``orders``.
  2. **Un solo flusher** (:func:`flush_capi_outbox`) lee el outbox, aplica las
     guardas (config, ``ctwa_clid``, ventana de 7 días, lock terminal,
     idempotencia), hace el POST y persiste el resultado en
     ``metadata["capi_events_sent"]`` — TAMBIÉN los skips.
  3. Se invoca oportunistamente desde varios lugares durables (activity del
     workflow de Sales tras cada turno, activity del watchdog, activity de
     cambio de etapa de orders) y best-effort desde la API humana. Una
     entrada pendiente sobrevive en el vault hasta que algún flush la cierre.

Política de errores (lección L-1 del ConnectorKit):
  * 4xx → final (``failed_4xx``), no reintentar.
  * 5xx / connect-error (la request NO llegó) → queda pendiente con
    ``attempts += 1``; tras ``MAX_FLUSH_ATTEMPTS`` se cierra ``failed_gave_up``.
  * read-timeout / error de protocolo DESPUÉS de enviar → ``unknown``. Meta
    pudo recibirlo; como no deduplica, reenviar duplicaría (un Purchase
    duplicado infla el ROAS). Se registra y no se reintenta.

Shape en ``metadata.json``::

    "capi_outbox": [{"event_id", "event_name", "episode_id", "order_id",
                     "value", "currency", "source", "queued_at_ms",
                     "attempts", "last_error"}],
    "capi_events_sent": [{"event_id", "event_name", "status", "http_status",
                          "error_detail", "fbtrace_id", "at_ms", "source"}],
    "capi_terminal_event": "Purchase"   # lock tras un Purchase enviado
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import structlog

from src.platform.whatsapp.capi import (
    CAPI_EVENT_NAMES,
    META_CAPI_API_URL,
    PRE_PURCHASE_EVENT_NAMES,
    build_capi_event,
    build_capi_request_body,
    is_ctwa_clid_within_attribution_window,
    make_event_id,
    validate_event_name,
)

log = structlog.get_logger()

OUTBOX_KEY = "capi_outbox"
SENT_KEY = "capi_events_sent"
TERMINAL_KEY = "capi_terminal_event"

#: Reintentos para errores transitorios (5xx / connect). Con flush por turno,
#: watchdog y etapas del pedido, 8 intentos cubren varios días de Meta caída.
MAX_FLUSH_ATTEMPTS: int = 8

_HTTP_TIMEOUT_S: float = 15.0

Poster = Callable[[str, dict[str, Any], str], Awaitable[httpx.Response]]


# =============================================================================
# Config (explícita — la activity la arma con sus propios globals, la API
# con ``load_capi_config``)
# =============================================================================


@dataclass(frozen=True)
class CapiConfig:
    dataset_id: str
    access_token: str
    waba_id: str
    test_event_code: str
    vault_dir: Path

    @property
    def enabled(self) -> bool:
        return bool(self.dataset_id and self.access_token)


def load_capi_config() -> CapiConfig:
    """Config desde ``src.platform.config`` (lectura en call-time para que los
    tests puedan parchear el módulo de config)."""
    from src.platform import config as cfg

    return CapiConfig(
        dataset_id=cfg.META_CAPI_DATASET_ID,
        access_token=cfg.META_CAPI_ACCESS_TOKEN,
        waba_id=cfg.WHATSAPP_BUSINESS_ACCOUNT_ID,
        test_event_code=cfg.META_CAPI_TEST_EVENT_CODE,
        vault_dir=Path(cfg.WORKSPACE_VAULT_DIR),
    )


@dataclass(frozen=True)
class CapiFlushSummary:
    """Resumen JSON-plano del flush (R-JSON) — lo que devuelve la activity.
    Sin ``Any`` en las anotaciones: el payload converter de Temporal evalúa
    los type hints dentro del sandbox del workflow."""

    session_id: str
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    pending: int = 0


@dataclass(frozen=True)
class CapiFlushResult:
    session_id: str
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    pending: int = 0
    outcomes: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def summary(self) -> CapiFlushSummary:
        return CapiFlushSummary(
            session_id=self.session_id,
            sent=self.sent,
            skipped=self.skipped,
            failed=self.failed,
            pending=self.pending,
        )

    def outcome_for(self, event_id: str) -> dict[str, Any] | None:
        for o in self.outcomes:
            if o.get("event_id") == event_id:
                return o
        return None


# =============================================================================
# Puro — sobre el dict de metadata
# =============================================================================


def has_ctwa_attribution(metadata: dict[str, Any]) -> bool:
    """True si la sesión entró por un anuncio CTWA con ``ctwa_clid``."""
    clid, _ = resolve_ctwa_attribution(metadata)
    return clid is not None


def resolve_ctwa_attribution(
    metadata: dict[str, Any],
) -> tuple[str | None, int | None]:
    """``(ctwa_clid, captured_at_ms)`` del último toque CTWA, o ``(None, None)``.

    Último toque = atribución last-click (la que usa Meta). El array lo
    escribe ``IngestInboundMessage._handle_referral`` solo cuando el referral
    trae ``ctwa_clid``.
    """
    referrals = metadata.get("ctwa_referrals", [])
    if not isinstance(referrals, list) or not referrals:
        return None, None
    last = referrals[-1]
    if not isinstance(last, dict):
        return None, None
    clid = last.get("ctwa_clid")
    captured = last.get("captured_at_ms")
    if not isinstance(clid, str) or not clid or not isinstance(captured, int):
        return None, None
    return clid, captured


def pending_capi_events(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    outbox = metadata.get(OUTBOX_KEY)
    if not isinstance(outbox, list):
        return []
    return [e for e in outbox if isinstance(e, dict) and e.get("event_id")]


def _already_finalized(metadata: dict[str, Any], event_id: str) -> bool:
    """Ya salió (``sent``) o quedó en estado ambiguo (``unknown``): no se
    vuelve a mandar bajo ningún concepto."""
    sent = metadata.get(SENT_KEY)
    if not isinstance(sent, list):
        return False
    return any(
        isinstance(e, dict)
        and e.get("event_id") == event_id
        and e.get("status") in ("sent", "unknown")
        for e in sent
    )


def enqueue_capi_event(
    metadata: dict[str, Any],
    *,
    event_name: str,
    session_id: str,
    source: str,
    now_ms: int,
    episode_id: str | None = None,
    order_id: str | None = None,
    value: int | None = None,
    currency: str | None = None,
) -> str | None:
    """Encola un evento (idempotente). Devuelve el ``event_id`` si quedó
    encolado, ``None`` si no aplica (sesión sin atribución CTWA) o ya estaba
    encolado / enviado. Levanta ``ValueError`` con nombres inválidos.

    No hace I/O: el caller persiste el ``metadata`` como ya lo hacía.
    """
    validate_event_name(event_name)
    if not has_ctwa_attribution(metadata):
        return None
    event_id = make_event_id(
        event_name, session_id=session_id, episode_id=episode_id, order_id=order_id
    )
    if _already_finalized(metadata, event_id):
        return None
    outbox = metadata.get(OUTBOX_KEY)
    if not isinstance(outbox, list):
        outbox = []
        metadata[OUTBOX_KEY] = outbox
    if any(isinstance(e, dict) and e.get("event_id") == event_id for e in outbox):
        return None
    entry: dict[str, Any] = {
        "event_id": event_id,
        "event_name": event_name,
        "episode_id": episode_id,
        "order_id": order_id,
        "value": value,
        "currency": currency,
        "source": source,
        "queued_at_ms": now_ms,
        "attempts": 0,
        "last_error": None,
    }
    outbox.append(entry)
    return event_id


# =============================================================================
# I/O — metadata.json + HTTP
# =============================================================================


def _metadata_path(vault_dir: Path, session_id: str) -> Path:
    return Path(vault_dir) / session_id / "metadata.json"


def _read_metadata(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_metadata(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


async def _default_post(url: str, body: dict[str, Any], token: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
        return await client.post(
            url,
            params={"access_token": token},
            headers={"Content-Type": "application/json"},
            json=body,
        )


def _is_ambiguous_transport_error(exc: BaseException) -> bool:
    """La request PUDO haber llegado a Meta (respuesta perdida)."""
    return isinstance(
        exc,
        (httpx.ReadTimeout, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError, httpx.PoolTimeout),
    )


def _fbtrace(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    trace = body.get("fbtrace_id")
    if trace:
        return str(trace)
    err = body.get("error")
    if isinstance(err, dict) and err.get("fbtrace_id"):
        return str(err["fbtrace_id"])
    return None


def _record(
    metadata: dict[str, Any],
    entry: dict[str, Any],
    *,
    status: str,
    now_ms: int,
    http_status: int | None = None,
    error_detail: str | None = None,
    fbtrace_id: str | None = None,
) -> dict[str, Any]:
    sent = metadata.get(SENT_KEY)
    if not isinstance(sent, list):
        sent = []
        metadata[SENT_KEY] = sent
    outcome = {
        "event_id": entry["event_id"],
        "event_name": entry["event_name"],
        "status": status,
        "http_status": http_status,
        "error_detail": error_detail,
        "fbtrace_id": fbtrace_id,
        "at_ms": now_ms,
        "source": entry.get("source"),
    }
    sent.append(outcome)
    if status == "sent" and entry["event_name"] == "Purchase":
        metadata[TERMINAL_KEY] = "Purchase"
    return outcome


async def flush_capi_outbox(
    session_id: str,
    *,
    config: CapiConfig | None = None,
    post: Poster | None = None,
    now_ms: int | None = None,
) -> CapiFlushResult:
    """Envía todo lo pendiente del outbox de ``session_id``.

    Idempotente y seguro de llamar desde cualquier proceso: sin outbox no
    toca el archivo. Cada entrada termina en exactamente uno de:
    ``sent`` / ``skipped_*`` / ``failed_4xx`` / ``failed_gave_up`` /
    ``unknown`` (y sale del outbox), o sigue pendiente (5xx / connect).
    """
    cfg = config or load_capi_config()
    poster = post or _default_post
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    path = _metadata_path(cfg.vault_dir, session_id)
    metadata = _read_metadata(path)
    if metadata is None:
        return CapiFlushResult(session_id=session_id)
    pending = pending_capi_events(metadata)
    if not pending:
        return CapiFlushResult(session_id=session_id)

    clid, captured_at_ms = resolve_ctwa_attribution(metadata)
    outcomes: list[dict[str, Any]] = []
    sent = skipped = failed = 0
    remaining: list[dict[str, Any]] = []

    for entry in pending:
        name = entry["event_name"]
        event_id = entry["event_id"]
        skip: str | None = None
        if name not in CAPI_EVENT_NAMES:
            skip = "skipped_invalid_event"
        elif not cfg.enabled:
            skip = "skipped_no_config"
        elif not cfg.waba_id:
            skip = "skipped_no_waba_id"
        elif clid is None or captured_at_ms is None:
            skip = "skipped_no_ctwa_clid"
        elif not is_ctwa_clid_within_attribution_window(received_at_ms=captured_at_ms, now_ms=now):
            skip = "skipped_attribution_expired"
        elif metadata.get(TERMINAL_KEY) == "Purchase" and name in PRE_PURCHASE_EVENT_NAMES:
            skip = "skipped_terminal_event_reached"
        elif _already_finalized(metadata, event_id):
            skip = "skipped_already_sent"
        elif name == "Purchase" and not isinstance(entry.get("value"), int):
            skip = "skipped_no_registered_order"

        if skip is not None:
            outcomes.append(_record(metadata, entry, status=skip, now_ms=now))
            skipped += 1
            log.info("capi_outbox_skipped", session_id=session_id, event_id=event_id, reason=skip)
            continue

        try:
            event = build_capi_event(
                event_name=name,
                event_time=int(entry.get("queued_at_ms") or now) // 1000,
                event_id=event_id,
                waba_id=cfg.waba_id,
                ctwa_clid=clid,  # type: ignore[arg-type]
                value=entry.get("value"),
                currency=entry.get("currency"),
            )
        except ValueError as exc:
            outcomes.append(_record(metadata, entry, status="failed_other", now_ms=now, error_detail=str(exc)))
            failed += 1
            continue

        body = build_capi_request_body(event, test_event_code=cfg.test_event_code or None)
        url = META_CAPI_API_URL.format(dataset_id=cfg.dataset_id)
        try:
            response = await poster(url, body, cfg.access_token)
        except Exception as exc:  # noqa: BLE001 — clasificación L-1 abajo
            if _is_ambiguous_transport_error(exc):
                outcomes.append(_record(metadata, entry, status="unknown", now_ms=now, error_detail=f"{type(exc).__name__}: {exc}"))
                failed += 1
                log.warning("capi_outbox_unknown_after_post", session_id=session_id, event_id=event_id, error=str(exc))
                continue
            failed += 1
            entry["attempts"] = int(entry.get("attempts") or 0) + 1
            entry["last_error"] = f"{type(exc).__name__}: {exc}"
            if entry["attempts"] >= MAX_FLUSH_ATTEMPTS:
                outcomes.append(_record(metadata, entry, status="failed_gave_up", now_ms=now, error_detail=entry["last_error"]))
            else:
                remaining.append(entry)
            log.warning("capi_outbox_transport_error", session_id=session_id, event_id=event_id, error=str(exc), attempts=entry["attempts"])
            continue

        try:
            body_json: Any = response.json()
        except (json.JSONDecodeError, ValueError):
            body_json = None
        status_code = response.status_code
        trace = _fbtrace(body_json)

        if 200 <= status_code < 300:
            received = int(body_json.get("events_received", 0)) if isinstance(body_json, dict) else 0
            if received < 1:
                outcomes.append(_record(metadata, entry, status="failed_other", now_ms=now, http_status=status_code, error_detail=f"200 OK but events_received={received}", fbtrace_id=trace))
                failed += 1
                log.error("capi_outbox_unexpected_response", session_id=session_id, event_id=event_id, body=body_json)
                continue
            outcomes.append(_record(metadata, entry, status="sent", now_ms=now, http_status=status_code, fbtrace_id=trace))
            sent += 1
            log.info("capi_send_success", session_id=session_id, event_id=event_id, event_name=name, ctwa_clid=clid, fbtrace_id=trace)
            continue

        detail = json.dumps(body_json) if body_json is not None else "no_body"
        if 400 <= status_code < 500:
            outcomes.append(_record(metadata, entry, status="failed_4xx", now_ms=now, http_status=status_code, error_detail=detail, fbtrace_id=trace))
            failed += 1
            log.error("capi_send_failed_4xx", session_id=session_id, event_id=event_id, http_status=status_code, body=body_json, fbtrace_id=trace)
            continue

        failed += 1
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        entry["last_error"] = f"http {status_code}: {detail[:200]}"
        if entry["attempts"] >= MAX_FLUSH_ATTEMPTS:
            outcomes.append(_record(metadata, entry, status="failed_gave_up", now_ms=now, http_status=status_code, error_detail=detail, fbtrace_id=trace))
        else:
            remaining.append(entry)
        log.warning("capi_send_failed_5xx", session_id=session_id, event_id=event_id, http_status=status_code, attempts=entry["attempts"])

    metadata[OUTBOX_KEY] = remaining
    _write_metadata(path, metadata)
    return CapiFlushResult(
        session_id=session_id,
        sent=sent,
        skipped=skipped,
        failed=failed,
        pending=len(remaining),
        outcomes=tuple(outcomes),
    )


# =============================================================================
# Fire-and-forget con referencia fuerte (para handlers HTTP)
# =============================================================================

_background: set[asyncio.Task[Any]] = set()


def schedule_capi_flush(session_id: str) -> None:
    """Flush best-effort desde un handler HTTP (acciones humanas). Si falla o
    el proceso muere, la entrada sigue en el outbox y la cierra el próximo
    flush durable (turno de Sales, watchdog o cambio de etapa del pedido)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run() -> None:
        try:
            await flush_capi_outbox(session_id)
        except Exception as exc:  # noqa: BLE001 — best-effort, el outbox persiste
            log.warning("capi_outbox_background_flush_failed", session_id=session_id, error=str(exc))

    task = loop.create_task(_run())
    _background.add(task)
    task.add_done_callback(_background.discard)


__all__ = [
    "OUTBOX_KEY",
    "SENT_KEY",
    "TERMINAL_KEY",
    "MAX_FLUSH_ATTEMPTS",
    "CapiConfig",
    "CapiFlushResult",
    "CapiFlushSummary",
    "load_capi_config",
    "has_ctwa_attribution",
    "resolve_ctwa_attribution",
    "pending_capi_events",
    "enqueue_capi_event",
    "flush_capi_outbox",
    "schedule_capi_flush",
]
