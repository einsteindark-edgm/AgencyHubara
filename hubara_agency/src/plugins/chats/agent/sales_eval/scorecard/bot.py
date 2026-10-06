"""Qué bot respondió un episodio de producción.

El bot Jev es el workflow nuevo (decisión del operador, 2026-10-02; antes se
miraba si las capas de percepción actuaban, `mode: on/canary`, y una
conversación del workflow nuevo con las capas apagadas contaba como del bot
actual). Lo dice la traza de cada turno del cliente (`workflow`, que escribe
`persist_turn_trace`); las de antes no lo traen, pero el workflow nuevo
siempre deja la salida de Jev (`egress`) y el actual nunca. El complemento y
el ghosting no dicen qué bot respondió al cliente.

Un episodio con turnos de los dos workflows (se encendió o apagó a mitad de
la conversación) es "mixto": no se le carga a ninguno.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

BOTS = ("actual", "nuevo")
MIXED = "mixto"
#: El workflow de cada bot (`workflow` en la traza del turno).
_BOT_OF_WORKFLOW = {"v1": "actual", "v2": "nuevo"}


def turn_workflow(trace: dict[str, Any]) -> str:
    """`v2` (el workflow nuevo, el bot Jev) o `v1` (el actual)."""
    workflow = trace.get("workflow")
    if workflow in _BOT_OF_WORKFLOW:
        return str(workflow)
    return "v2" if "egress" in trace else "v1"


def episode_bot(traces: Iterable[dict[str, Any]]) -> str:
    bots = {
        _BOT_OF_WORKFLOW[turn_workflow(trace)]
        for trace in traces
        if isinstance(trace, dict) and trace.get("trigger", "customer") == "customer"
    }
    if len(bots) > 1:
        return MIXED
    return bots.pop() if bots else "actual"
