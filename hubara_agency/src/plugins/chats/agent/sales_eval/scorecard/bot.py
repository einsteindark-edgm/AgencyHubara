"""Qué bot respondió un episodio de producción (plan del laboratorio PR 18).

Durante el encendido por etapas, Calidad LLM separa los episodios del bot
nuevo (capas con clasificador) de los del actual. Sale de la traza de cada
turno (`mode`): el bot nuevo actúa con `on` o `canary`; en `shadow` el
clasificador solo mide y la respuesta es la del bot actual.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

BOTS = ("actual", "nuevo")
_ACTING_MODES = frozenset({"on", "canary"})


def episode_bot(traces: Iterable[dict[str, Any]]) -> str:
    return "nuevo" if any(isinstance(t, dict) and t.get("mode") in _ACTING_MODES for t in traces) else "actual"
