"""Activities del ciclo order-sentinel (plugin order_sentinel).

Solo imports `src.sdk` (P-28). El ÚNICO lugar con I/O del plugin (perfil sync,
P-29) — el plan puro vive en `agent/cycle/use_cases/`. Cuatro seams:
  * `build_order_sentinel_snapshot_activity` — vault scan (tag HUMANO + orden
    + watermark) + enriquecimiento stage/pago vía la API de orders.
  * `dispatch_order_sentinel_activity` — despierta la caja GraphAgents y
    despacha el run (bridge poll-based, graphagentskit). Worst-case minutos
    (cold start EC2) → heartbeat (R-HEARTBEAT).
  * `poll_order_sentinel_activity` — UN poll del estado del run
    (fetch_status + interpret puro). El LOOP vive en el workflow (durable).
  * `execute_order_intents_activity` — la AUTORIDAD: ejecuta los intents del
    agente contra la API HTTP de orders (validación DAG real, SSE dashboard)
    con `notify_customer=false` (el humano ya avisó por chat — la cascada ETA
    duplicaría el mensaje) y cierra los watermarks por sesión.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from temporalio import activity

from src.plugins.order_sentinel.agent.cycle.composition import get_launcher
from src.plugins.order_sentinel.agent.cycle.use_cases import (
    build_snapshot_from_sessions,
)
from src.sdk.runtime import WORKSPACE_VAULT_DIR, with_heartbeat

#: prefijo de sesiones WhatsApp en el vault (los demás dirs se saltan).
_SESSION_PREFIX = "wa_"

#: archivo de watermark PROPIO del sentinel en el dir de la sesión — NO va en
#: metadata.json (muchos escritores concurrentes; este archivo es solo nuestro).
_WATERMARK_FILE = "order_sentinel.json"
_WATERMARK_KEY = "last_analyzed_at_ms"


def _api_base() -> str:
    # Default = el nombre de servicio del compose (worker→API cross-container).
    # NO loopback: el gate castkit_loopback prohíbe httpx+:8000 en plugins —
    # para dev fuera del stack, seteá HUBARA_API_BASE_URL explícito.
    return os.environ.get("HUBARA_API_BASE_URL", "http://hubara-api:8000").rstrip("/")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_history_events(session_dir: Path) -> list[dict[str, Any]]:
    """Las líneas del JSONL de FilesystemMessageHistoryStore
    (`<session>/sessions/<session>.jsonl`) — corruptas se saltan, no tumban."""
    history = session_dir / "sessions" / f"{session_dir.name}.jsonl"
    if not history.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = history.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _read_watermark(session_dir: Path) -> int | None:
    data = _read_json(session_dir / _WATERMARK_FILE) or {}
    value = data.get(_WATERMARK_KEY)
    return value if isinstance(value, int) else None


def _write_watermark(session_dir: Path, at_ms: int) -> None:
    """Escritura atómica (tmp + os.replace) del archivo propio del sentinel."""
    tmp = session_dir / f".{_WATERMARK_FILE}.tmp"
    tmp.write_text(json.dumps({_WATERMARK_KEY: at_ms}), encoding="utf-8")
    os.replace(tmp, session_dir / _WATERMARK_FILE)


def _sanitize_note(evidence: Any) -> str:
    """La evidencia es texto arbitrario del cliente/LLM (PM-020): a la note de
    auditoría de la orden va como str, whitespace colapsado y truncada."""
    text = re.sub(r"\s+", " ", str(evidence)).strip()
    return text[:200]


async def _fetch_order_state(
    client: httpx.AsyncClient, order_id: str
) -> tuple[str, bool] | None:
    """`GET /api/orders/{id}` → (current_stage, payment_confirmed). None si la
    orden no se pudo leer — mejor excluir que analizar con estado inventado.
    El fallo se LOGUEA (PM-003: tragado en silencio, un base URL roto se veía
    como `skipped_empty` eterno indistinguible de "no hay trabajo")."""
    try:
        # Ruta REAL: prefix /api/orders del manifest + path /orders del router
        # → /api/orders/orders/{id} (así la consume el frontend). Con un solo
        # /orders es 404 DE RUTA para toda orden (incidente prod 2026-07-10:
        # respx no lo caza porque mockea la URL que el código pida).
        response = await client.get(
            f"{_api_base()}/api/orders/orders/{quote(order_id, safe='')}"
        )
        response.raise_for_status()
        summary = response.json().get("summary") or {}
    except (httpx.HTTPError, ValueError) as e:
        activity.logger.warning(
            "order-sentinel: GET /orders/%s falló (%s: %s) — conversación "
            "EXCLUIDA del snapshot (¿HUBARA_API_BASE_URL correcto?).",
            order_id,
            type(e).__name__,
            e,
        )
        return None
    stage = summary.get("status")
    if not isinstance(stage, str):
        return None
    return stage, summary.get("pay_status") == "paid"


@activity.defn(name="build_order_sentinel_snapshot")
async def build_order_sentinel_snapshot_activity() -> dict[str, Any]:
    """Escanea el vault y arma el seed del order-sentinel (I/O acá; la
    transformación pura en use_cases.build_snapshot), enriqueciendo cada
    conversación con el estado REAL de su orden vía la API de orders."""
    now_ms = int(time.time() * 1000)
    sessions: list[tuple[str, dict[str, Any], list[dict[str, Any]], int | None]] = []
    vault = WORKSPACE_VAULT_DIR
    if vault.exists():
        for session_dir in sorted(vault.iterdir()):
            if not session_dir.is_dir():
                continue
            if not session_dir.name.startswith(_SESSION_PREFIX):
                continue
            metadata = _read_json(session_dir / "metadata.json")
            if metadata is None:
                continue
            sessions.append(
                (
                    session_dir.name,
                    metadata,
                    _read_history_events(session_dir),
                    _read_watermark(session_dir),
                )
            )
    snapshot = build_snapshot_from_sessions(now_ms, sessions)

    # Enriquecimiento: el stage/pago REAL por orden. Un GET fallido EXCLUYE la
    # conversación (y su watermark: no la analizamos — el próximo ciclo reintenta).
    enriched: list[dict[str, Any]] = []
    excluded = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for convo in snapshot["conversations"]:
            state = await _fetch_order_state(client, convo["order_id"])
            if state is None:
                snapshot["watermarks"].pop(convo["session_id"], None)
                excluded += 1
                continue
            convo["current_stage"], convo["payment_confirmed"] = state
            enriched.append(convo)
    snapshot["conversations"] = enriched
    # PM-009: los recortes viajan en el snapshot (nada de silent caps) — el
    # workflow los loguea y quedan visibles en el history de Temporal.
    snapshot["excluded_orders"] = excluded
    if excluded or snapshot.get("truncated_sessions"):
        activity.logger.warning(
            "order-sentinel snapshot: %s conversaciones excluidas por orden "
            "ilegible, %s sesiones truncadas por el cap del ciclo.",
            excluded,
            snapshot.get("truncated_sessions", 0),
        )
    return snapshot


@activity.defn(name="dispatch_order_sentinel")
@with_heartbeat(every=10)
async def dispatch_order_sentinel_activity(
    snapshot: dict[str, Any], run_id: str
) -> str:
    """Despierta la caja (cold start EC2 1-3 min) y despacha el run.
    Devuelve el execution-id de Conductor para pollearlo."""
    launcher = get_launcher()
    # PM-007: el launcher es boto3 SYNC (waiters + sleep) — en el event loop
    # bloquearía el heartbeat asyncio y el cold start (1-3 min) moriría por
    # HEARTBEAT_TIMEOUT. A thread.
    await asyncio.to_thread(launcher.start_box)
    # El contrato del agente (manifest inputs + golden order-sentinel) espera
    # el snapshot ENVUELTO: {"payload": snapshot}. Mandarlo crudo hace fallar
    # el nodo ingest con "falta 'payload'" (gotcha PR #148, run prod ce80f73f).
    return await asyncio.to_thread(
        launcher.dispatch, "order-sentinel", {"payload": snapshot}, run_id=run_id
    )


@activity.defn(name="poll_order_sentinel")
async def poll_order_sentinel_activity(execution_id: str) -> dict[str, Any]:
    """UN poll: JSON crudo de Conductor → estado lógico (interpret puro)."""
    from src.sdk.graphagentskit import interpret

    launcher = get_launcher()
    # boto3 sync → thread (PM-007): no bloquear el event loop del worker.
    return interpret(await asyncio.to_thread(launcher.fetch_status, execution_id))


async def _execute_intent(client: httpx.AsyncClient, intent: dict[str, Any]) -> str:
    """UN intent → 'applied' | 'skipped' | 'failed'. `invalid_transition` es la
    carrera BENIGNA con el humano (ya movió la tarjeta) — skipped, no failed."""
    order_id = str(intent.get("order_id") or "")
    action = intent.get("action")
    base = _api_base()
    oid = quote(order_id, safe="")  # PM-023: un id raro no rompe la ruta
    try:
        if action == "transition":
            evidence = intent.get("evidence") or []
            first = _sanitize_note(evidence[0]) if evidence else ""
            note = f"order-sentinel: {first}" if first else "order-sentinel"
            response = await client.patch(
                f"{base}/api/orders/orders/{oid}/stage",
                json={
                    "stage": intent.get("to_stage"),
                    "note": note,
                    "by": "order-sentinel",
                    "notify_customer": False,
                },
            )
        elif action == "confirm_payment":
            response = await client.patch(
                f"{base}/api/orders/orders/{oid}/confirm-payment",
                json={"by": "order-sentinel"},
            )
        else:
            activity.logger.warning(
                "order-sentinel: intent con action desconocida %r (orden %s)",
                action,
                order_id,
            )
            return "failed"
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as e:
        activity.logger.warning(
            "order-sentinel: intent %s/%s falló: %s", action, order_id, e
        )
        return "failed"
    if result.get("success"):
        return "applied"
    detail = str(result.get("error_detail") or "")
    # invalid_transition: el humano ya movió la tarjeta (carrera benigna).
    # invalid_state: la orden es un draft aún no agendado (PM-011) — también
    # benigno, el humano agenda y el próximo ciclo (si hay señal nueva) aplica.
    if detail.startswith(("invalid_transition", "invalid_state")):
        return "skipped"
    activity.logger.warning(
        "order-sentinel: intent %s/%s rechazado: %s", action, order_id, detail
    )
    return "failed"


@activity.defn(name="execute_order_intents")
async def execute_order_intents_activity(
    intents: list[dict[str, Any]], watermarks: dict[str, int]
) -> dict[str, Any]:
    """Ejecuta los intents contra la API de orders y cierra los watermarks de
    TODAS las sesiones analizadas (con o sin intents, aplicados o no: el
    watermark es "hasta dónde analicé", no "hasta dónde apliqué")."""
    summary = {"applied": 0, "skipped": 0, "failed": 0}
    async with httpx.AsyncClient(timeout=15) as client:
        for intent in intents:
            summary[await _execute_intent(client, intent)] += 1

    for session_id, at_ms in watermarks.items():
        session_dir = WORKSPACE_VAULT_DIR / session_id
        if session_dir.is_dir():
            _write_watermark(session_dir, at_ms)
    return summary
