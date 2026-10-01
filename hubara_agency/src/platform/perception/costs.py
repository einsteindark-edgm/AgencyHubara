"""El costo de Jev, por conversación (como el del LLM y el de WhatsApp).

Cada pregunta a Jev trae lo que cobró OpenRouter (`PerceptionResult.cost_usd`,
de `usage.cost`). Quien pregunta por una conversación lo suma acá al episodio
en `metadata.json`:

    episodes[].jev_usage = {"calls": <preguntas>, "cost_usd_micros": <micro-USD>}

Micro-USD enteros (lección cost-unit: una pregunta cuesta ~2e-5 USD; en
centavos se pierde). Va al episodio abierto; si no hay, al último (remarketing
pregunta sobre una conversación ya cerrada y el costo es de esa
conversación). Ads lo lee del vault («Costo Jev»).

Nunca crea la sesión: la prueba diaria de Jev usa una sesión sintética que no
está en el vault. Escribe con `FilesystemMetadataStore.update` (lock por
sesión): el ingest y los workers escriben el mismo archivo.

Cada llamada es una pregunta realmente cobrada: un reintento de la activity
vuelve a preguntar y vuelve a cobrar, así que no se deduplica.
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


def apply_jev_cost(metadata: dict[str, Any], micros: int) -> bool:
    """Suma una pregunta y su costo al episodio (muta `metadata`). Pura."""
    episode = _episode_for_cost(metadata)
    if episode is None or micros <= 0:
        return False
    usage = episode.get("jev_usage")
    if not isinstance(usage, dict):
        usage = {"calls": 0, "cost_usd_micros": 0}
    episode["jev_usage"] = {
        "calls": int(usage.get("calls") or 0) + 1,
        "cost_usd_micros": int(usage.get("cost_usd_micros") or 0) + micros,
    }
    return True


def record_jev_cost(session_id: str | None, cost_usd: float | None, *, vault_dir: Path | None = None) -> bool:
    """Suma lo que costó una pregunta a Jev a la conversación. Nunca lanza:
    registrar el costo no frena la decisión. Devuelve si lo registró."""
    micros = usd_to_micros(cost_usd)
    if not session_id or micros <= 0 or not is_vault_session_id(session_id):
        return False
    if vault_dir is None:
        from src.platform.config import WORKSPACE_VAULT_DIR

        vault_dir = Path(WORKSPACE_VAULT_DIR)
    store = FilesystemMetadataStore(Path(vault_dir))
    if not (Path(vault_dir) / session_id / "metadata.json").is_file():
        return False
    applied = False

    def mutate(metadata: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal applied
        # Lectura vacía (archivo ilegible un instante): no pisar el estado real.
        if not metadata:
            return None
        applied = apply_jev_cost(metadata, micros)
        return metadata if applied else None

    try:
        store.update(session_id, mutate)
    except OSError as exc:
        logger.warning("perception.jev_cost_not_recorded", error=repr(exc)[:200])
        return False
    return applied
