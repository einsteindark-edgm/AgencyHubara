"""Índice incremental de reactivación — el shortlist del Window Strategist.

Punto 2 del plan de escala: con millones de conversaciones, el scan O(N) del
vault por ciclo no aguanta. Este índice liviano (`_analytics/
reengagement_index.json`: sesión → ventanas + flags de lead) lo actualiza el
ingest EN CADA INBOUND (mismo momento en que estampa las ventanas), y el
snapshot builder solo abre el `metadata.json` de los candidatos del
shortlist. El costo por ciclo sigue al conjunto ACCIONABLE, no al vault.

Contrato de staleness (lo que hace esto seguro): el índice NUNCA decide —
solo shortlistea. La decisión real corre sobre metadata REAL (pre-filtro con
la central) y el gate del RemarketingWorkflow re-valida al ejecutar. Una
entrada stale a lo sumo agrega una lectura de más (falso candidato) o
posterga a un lead cuyo estado cambió sin inbound — acotado por la regla
"entrada joven" (<72h siempre candidata) y el rebuild (sin índice → full
scan que lo reconstruye; el builder refresca toda entrada que lee).

Expuesto a plugins vía `src.sdk.messagingkit` (P-28).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.platform.state import atomic_write_json
from src.platform.whatsapp.send_policy import lead_state_from_metadata

#: dónde vive el índice, relativo al vault (junto a la otra data derivada).
INDEX_RELPATH = "_analytics/reengagement_index.json"

#: entrada más joven que esto = candidata SIEMPRE (el estado puede estar
#: cambiando rápido tras el último inbound: órdenes, tags, handoffs).
YOUNG_ENTRY_MS = 72 * 60 * 60 * 1000


def _index_path(vault_dir: Path) -> Path:
    return Path(vault_dir) / INDEX_RELPATH


def index_entry_from_metadata(
    metadata: dict[str, Any], *, now_ms: int
) -> dict[str, Any]:
    """metadata.json → la entrada liviana del índice (pura)."""
    lead = lead_state_from_metadata(metadata)
    return {
        "last_inbound_at_ms": metadata.get("last_inbound_at_ms"),
        "service_window_expires_at_ms": metadata.get(
            "service_window_expires_at_ms"
        ),
        "ctwa_window_expires_at_ms": metadata.get("ctwa_window_expires_at_ms"),
        "tag": lead.tag,
        "transactional_hook": lead.transactional_hook,
        "updated_at_ms": now_ms,
    }


def load_index(vault_dir: Path) -> dict[str, dict[str, Any]] | None:
    """El índice completo, o None si no existe / está roto (→ full scan)."""
    path = _index_path(vault_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def update_index_entry(
    vault_dir: Path, session_id: str, metadata: dict[str, Any], *, now_ms: int
) -> None:
    """Actualiza (read-modify-write atómico) la entrada de UNA sesión."""
    index = load_index(vault_dir) or {}
    index[session_id] = index_entry_from_metadata(metadata, now_ms=now_ms)
    path = _index_path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, index)


def update_index_entries(
    vault_dir: Path,
    entries: dict[str, dict[str, Any]],
    *,
    now_ms: int,
) -> None:
    """Batch del anterior (el builder refresca todo lo que leyó en UN write)."""
    if not entries:
        return
    index = load_index(vault_dir) or {}
    for session_id, metadata in entries.items():
        index[session_id] = index_entry_from_metadata(metadata, now_ms=now_ms)
    path = _index_path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, index)


def _in_window(now_ms: int, expires_at_ms: Any) -> bool:
    return isinstance(expires_at_ms, int) and now_ms < expires_at_ms


def shortlist_session_ids(
    index: dict[str, dict[str, Any]], *, now_ms: int
) -> list[str]:
    """Candidatos a snapshot: ventana abierta, gancho vivo, o entrada joven.

    El caso masivo a escala (frío + viejo) queda fuera SIN abrir su metadata.
    Deliberadamente NO mira el tag: un HUMANO/convertido joven igual entra y
    lo excluye el pre-filtro con metadata real (barato para pocos; el tag
    puede cambiar sin inbound y el índice no se enteraría).
    """
    out: list[str] = []
    for session_id, e in index.items():
        if (
            _in_window(now_ms, e.get("service_window_expires_at_ms"))
            or _in_window(now_ms, e.get("ctwa_window_expires_at_ms"))
            or e.get("transactional_hook")
            or (
                isinstance(e.get("updated_at_ms"), int)
                and now_ms - e["updated_at_ms"] < YOUNG_ENTRY_MS
            )
        ):
            out.append(session_id)
    return out
