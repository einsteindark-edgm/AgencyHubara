"""Activities del ciclo Window Strategist (plugin reengagement).

Solo imports `src.sdk` (P-28). El ÚNICO lugar con I/O del plugin (perfil sync,
P-29) — el plan puro vive en `agent/use_cases/`. Tres seams:
  * `build_reengagement_snapshot_activity` — vault scan → el seed del agente.
  * `dispatch_window_strategist_activity` — despierta la caja GraphAgents y
    despacha el run (bridge poll-based, graphagentskit). Worst-case minutos
    (cold start EC2) → heartbeat (R-HEARTBEAT).
  * `poll_window_strategist_activity` — UN poll del estado del run
    (fetch_status + interpret puro). El LOOP vive en el workflow (durable).
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from temporalio import activity

from src.plugins.reengagement.agent.cycle.composition import get_launcher
from src.plugins.reengagement.agent.cycle.use_cases import build_snapshot_from_sessions
from src.sdk.messagingkit import (
    get_current_rate_card,
    is_quiet_hours_for_session,
    load_reengagement_index,
    reengagement_shortlist,
    update_reengagement_index_entries,
)
from src.sdk.runtime import WORKSPACE_VAULT_DIR, with_heartbeat

#: prefijo de sesiones WhatsApp en el vault (los demás dirs se saltan).
_SESSION_PREFIX = "wa_"


def _read_metadata(session_dir) -> dict[str, Any] | None:
    metadata_file = session_dir / "metadata.json"
    if not metadata_file.exists():
        return None
    try:
        return json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _full_scan(vault) -> list[tuple[str, dict[str, Any]]]:
    sessions: list[tuple[str, dict[str, Any]]] = []
    if vault.exists():
        for session_dir in sorted(vault.iterdir()):
            if not session_dir.is_dir():
                continue
            if not session_dir.name.startswith(_SESSION_PREFIX):
                continue
            metadata = _read_metadata(session_dir)
            if metadata is not None:
                sessions.append((session_dir.name, metadata))
    return sessions


@activity.defn(name="build_reengagement_snapshot")
async def build_reengagement_snapshot_activity() -> dict[str, Any]:
    """Arma el seed del window-strategist (I/O acá; la transformación pura en
    use_cases.build_snapshot).

    Punto 2 (escala): con índice presente, SHORTLIST — solo se abre el
    metadata de los candidatos (ventana abierta / gancho / entrada joven); el
    frío+viejo masivo ni se lee. Sin índice: full scan que lo RECONSTRUYE.
    Toda entrada leída se refresca (staleness auto-sanadora). El índice nunca
    decide: la decisión es del pre-filtro sobre metadata real + el gate.
    """
    now_ms = int(time.time() * 1000)
    vault = WORKSPACE_VAULT_DIR

    index = load_reengagement_index(vault)
    if index is None:
        sessions = _full_scan(vault)
        index_size = len(sessions)
    else:
        candidates = sorted(reengagement_shortlist(index, now_ms=now_ms))
        sessions = []
        for session_id in candidates:
            metadata = _read_metadata(vault / session_id)
            if metadata is not None:
                sessions.append((session_id, metadata))
        index_size = len(index)

    # Refresh/rebuild: lo que se leyó queda al día en UN write atómico.
    update_reengagement_index_entries(
        vault, dict(sessions), now_ms=now_ms
    )

    # Quiet hours (hora LOCAL del cliente): de noche el seed sale vacío y el
    # ciclo no despierta la caja EC2. El gate re-valida al enviar igual.
    now_utc = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    snapshot = build_snapshot_from_sessions(
        now_ms,
        sessions,
        rate_card=get_current_rate_card(),
        quiet_checker=lambda sid: is_quiet_hours_for_session(sid, now_utc),
    )
    snapshot["shortlisted"] = len(sessions)
    snapshot["index_size"] = index_size
    return snapshot


@activity.defn(name="dispatch_window_strategist")
@with_heartbeat(every=10)
async def dispatch_window_strategist_activity(
    snapshot: dict[str, Any], run_id: str
) -> str:
    """Despierta la caja (cold start EC2 1-3 min) y despacha el run.
    Devuelve el execution-id de Conductor para pollearlo."""
    launcher = get_launcher()
    # PM-007 (premortem order-sentinel): el launcher es boto3 SYNC (waiters +
    # sleep) — en el event loop bloquearía el heartbeat asyncio y el cold
    # start (1-3 min) moriría por HEARTBEAT_TIMEOUT. A thread.
    await asyncio.to_thread(launcher.start_box)
    # El contrato del agente (manifest inputs + case window-strategist-ciclo)
    # espera el snapshot ENVUELTO: {"payload": snapshot}. Mandarlo crudo hace
    # fallar el nodo ingest con "falta 'payload'" (run prod ce80f73f).
    return await asyncio.to_thread(
        launcher.dispatch, "window-strategist", {"payload": snapshot}, run_id=run_id
    )


@activity.defn(name="poll_window_strategist")
async def poll_window_strategist_activity(execution_id: str) -> dict[str, Any]:
    """UN poll: JSON crudo de Conductor → estado lógico (interpret puro)."""
    from src.sdk.graphagentskit import interpret

    launcher = get_launcher()
    try:
        # boto3 sync → thread (PM-007): no bloquear el event loop del worker.
        raw = await asyncio.to_thread(launcher.fetch_status, execution_id)
    except Exception as e:
        # Eslabón 9 de la cadena dispatch (PR #153): el autostop puede apagar
        # la caja con el resultado sin cosechar — la despertamos ANTES de que
        # Temporal reintente este poll, o el run queda inalcanzable (ciclos de
        # InvalidInstanceId para siempre).
        activity.logger.warning(
            "reengagement: poll de %s falló (%s: %s) — despierto la caja "
            "y dejo que el retry reintente.",
            execution_id,
            type(e).__name__,
            e,
        )
        await asyncio.to_thread(launcher.start_box)
        raise
    return interpret(raw)
