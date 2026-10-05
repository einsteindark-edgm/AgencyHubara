"""Lo que cuesta leer las fotos del cliente, por conversación.

Por foto, hasta tres llamadas a Gemini por el proxy: describirla (también lee
los comprobantes de pago), su huella (embedding) para buscarla en el catálogo
y la comparación contra las candidatas. El ingest las suma al episodio:
`episodes[].vision_usage = {calls, cost_usd_micros}` (ver
`platform/observability/episode_usage.py`). La huella se cobra por imagen
(`imagePrice` de la tabla: el proxy reporta 0 tokens para una imagen). Una
llamada sin precio conocido se cuenta igual, sin inventar el costo.

Fuera de una conversación no se registra: el índice de fotos del catálogo
es un costo de la tienda, y el laboratorio lee su banco.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.platform.observability.episode_usage import record_episode_usage

VISION_USAGE_FIELD = "vision_usage"


def record_vision_cost(
    session_id: str | None,
    cost_usd: float | None,
    *,
    calls: int = 1,
    vault_dir: Path | None = None,
    store: Any = None,
) -> bool:
    return record_episode_usage(session_id, VISION_USAGE_FIELD, cost_usd, calls=calls, vault_dir=vault_dir, store=store)
