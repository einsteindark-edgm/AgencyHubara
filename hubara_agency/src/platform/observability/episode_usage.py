"""Costos por conversación que no pasan por el bucle del agente.

`llm_usage` (el agente) y `cost_summary` (WhatsApp) tienen sus escritores;
los demás costos de una conversación se suman acá, con la MISMA forma:

    episodes[].<campo> = {"calls": <llamadas>, "cost_usd_micros": <micro-USD>}

  * `jev_usage`     las preguntas a Jev (platform/perception/costs.py)
  * `vision_usage`  leer las fotos del cliente (platform/vision/costs.py)
  * `audio_usage`   transcribir las notas de voz (platform/audio/costs.py)

Micro-USD enteros (lección cost-unit: son fracciones de centavo). Va al
episodio abierto o, si no hay, al último (remarketing pregunta sobre una
conversación cerrada; un comprobante llega con la conversación en manos de
una persona, sin episodio abierto). Nunca crea la sesión; escribe con
`update` (lock por sesión: el ingest y los workers escriben el mismo
archivo); nunca lanza.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import structlog

from src.platform.state import FilesystemMetadataStore, is_vault_session_id

logger = structlog.get_logger()


def usd_to_micros(cost_usd: float | None) -> int:
    if not cost_usd or cost_usd <= 0:
        return 0
    return int((Decimal(str(cost_usd)) * 1_000_000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _episode_for_cost(metadata: dict[str, Any]) -> dict[str, Any] | None:
    episodes = [e for e in metadata.get("episodes") or [] if isinstance(e, dict)]
    if not episodes:
        return None
    open_ = [e for e in episodes if e.get("closed_at_ms") is None]
    return open_[-1] if open_ else episodes[-1]


def apply_episode_usage(metadata: dict[str, Any], field: str, micros: int, calls: int) -> bool:
    """Suma llamadas y costo al episodio (muta `metadata`). Pura."""
    episode = _episode_for_cost(metadata)
    if episode is None or (micros <= 0 and calls <= 0):
        return False
    usage = episode.get(field)
    if not isinstance(usage, dict):
        usage = {"calls": 0, "cost_usd_micros": 0}
    episode[field] = {
        "calls": int(usage.get("calls") or 0) + max(calls, 0),
        "cost_usd_micros": int(usage.get("cost_usd_micros") or 0) + max(micros, 0),
    }
    return True


def record_episode_usage(
    session_id: str | None,
    field: str,
    cost_usd: float | None,
    *,
    calls: int,
    vault_dir: Path | None = None,
    store: Any = None,
) -> bool:
    """Suma `calls` llamadas que costaron `cost_usd` a la conversación.
    `store`: un metadata store con `update` (el del ingest); si no, el del
    vault. Devuelve si lo registró."""
    micros = usd_to_micros(cost_usd)
    if not session_id or (micros <= 0 and calls <= 0) or not is_vault_session_id(session_id):
        return False
    if store is None:
        if vault_dir is None:
            from src.platform.config import WORKSPACE_VAULT_DIR

            vault_dir = Path(WORKSPACE_VAULT_DIR)
        if not (Path(vault_dir) / session_id / "metadata.json").is_file():
            return False
        store = FilesystemMetadataStore(Path(vault_dir))
    applied = False

    def mutate(metadata: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal applied
        # Lectura vacía (archivo ilegible un instante): no pisar el estado real.
        if not metadata:
            return None
        applied = apply_episode_usage(metadata, field, micros, calls)
        return metadata if applied else None

    try:
        store.update(session_id, mutate)
    except Exception as exc:  # noqa: BLE001 — el costo nunca frena a quien lo registra
        logger.warning("episode_usage.not_recorded", field=field, error=repr(exc)[:200])
        return False
    return applied
