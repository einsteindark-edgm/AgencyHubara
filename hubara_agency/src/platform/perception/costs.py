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

from pathlib import Path

from src.platform.observability.episode_usage import record_episode_usage, usd_to_micros

__all__ = ["JEV_USAGE_FIELD", "record_jev_cost", "usd_to_micros"]

JEV_USAGE_FIELD = "jev_usage"


def record_jev_cost(session_id: str | None, cost_usd: float | None, *, vault_dir: Path | None = None) -> bool:
    """Suma lo que costó una pregunta a Jev a la conversación. Nunca lanza:
    registrar el costo no frena la decisión. Una pregunta sin costo (falló o
    el fake) no se cuenta. Devuelve si lo registró."""
    if usd_to_micros(cost_usd) <= 0:
        return False
    return record_episode_usage(session_id, JEV_USAGE_FIELD, cost_usd, calls=1, vault_dir=vault_dir)
