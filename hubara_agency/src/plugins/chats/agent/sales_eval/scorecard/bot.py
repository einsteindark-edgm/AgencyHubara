"""Qué bot respondió un episodio de producción (plan del laboratorio PR 18).

Durante el encendido por etapas, Calidad LLM separa los episodios del bot
nuevo (capas con clasificador) de los del actual. Sale de la traza de cada
turno del cliente (`mode`): el bot nuevo actúa con `on` o `canary`; en
`shadow` el clasificador solo mide y la respuesta es la del bot actual. El
complemento y el ghosting corren sin capas: no dicen qué bot respondió.

Un episodio con turnos de los dos bots (se subió o bajó de etapa a mitad de
la conversación) es "mixto": no se le carga a ninguno.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

BOTS = ("actual", "nuevo")
MIXED = "mixto"
_ACTING_MODES = frozenset({"on", "canary"})


def episode_bot(traces: Iterable[dict[str, Any]]) -> str:
    acting = other = False
    for trace in traces:
        if not isinstance(trace, dict) or trace.get("trigger", "customer") != "customer":
            continue
        if trace.get("mode") in _ACTING_MODES:
            acting = True
        else:
            other = True
    if acting and other:
        return MIXED
    return "nuevo" if acting else "actual"
