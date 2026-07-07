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

import json
import time
from typing import Any

from temporalio import activity

from src.plugins.reengagement.agent.cycle.composition import get_launcher
from src.plugins.reengagement.agent.cycle.use_cases import build_snapshot_from_sessions
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


@activity.defn(name="build_reengagement_snapshot")
async def build_reengagement_snapshot_activity() -> dict[str, Any]:
    """Escanea el vault y arma el seed del window-strategist (I/O acá; la
    transformación pura en use_cases.build_snapshot)."""
    now_ms = int(time.time() * 1000)
    sessions: list[tuple[str, dict[str, Any]]] = []
    vault = WORKSPACE_VAULT_DIR
    if vault.exists():
        for session_dir in sorted(vault.iterdir()):
            if not session_dir.is_dir():
                continue
            if not session_dir.name.startswith(_SESSION_PREFIX):
                continue
            metadata = _read_metadata(session_dir)
            if metadata is not None:
                sessions.append((session_dir.name, metadata))
    return build_snapshot_from_sessions(now_ms, sessions)


@activity.defn(name="dispatch_window_strategist")
@with_heartbeat(every=10)
async def dispatch_window_strategist_activity(
    snapshot: dict[str, Any], run_id: str
) -> str:
    """Despierta la caja (cold start EC2 1-3 min) y despacha el run.
    Devuelve el execution-id de Conductor para pollearlo."""
    launcher = get_launcher()
    launcher.start_box()
    return launcher.dispatch("window-strategist", snapshot, run_id=run_id)


@activity.defn(name="poll_window_strategist")
async def poll_window_strategist_activity(execution_id: str) -> dict[str, Any]:
    """UN poll: JSON crudo de Conductor → estado lógico (interpret puro)."""
    from src.sdk.graphagentskit import interpret

    launcher = get_launcher()
    return interpret(launcher.fetch_status(execution_id))
