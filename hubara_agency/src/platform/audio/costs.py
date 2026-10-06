"""Lo que cuesta transcribir las notas de voz del cliente, por conversación.

Una llamada a Gemini por nota de voz (alias `gemini-multimodal` del proxy). El
ingest la suma al episodio: `episodes[].audio_usage = {calls, cost_usd_micros}`
(ver `platform/observability/episode_usage.py`), como el costo del LLM, el de
WhatsApp, el de Jev y el de leer las fotos. Se cobra toda llamada que Google
contestó, también si no salió texto útil; una sin precio conocido se cuenta
igual, sin inventar el costo.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.platform.observability.episode_usage import record_episode_usage

AUDIO_USAGE_FIELD = "audio_usage"


def record_audio_cost(
    session_id: str | None,
    cost_usd: float | None,
    *,
    calls: int = 1,
    vault_dir: Path | None = None,
    store: Any = None,
) -> bool:
    return record_episode_usage(session_id, AUDIO_USAGE_FIELD, cost_usd, calls=calls, vault_dir=vault_dir, store=store)
