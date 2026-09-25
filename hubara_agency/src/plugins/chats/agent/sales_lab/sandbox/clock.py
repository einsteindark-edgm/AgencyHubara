"""Reloj del turno original dentro del sandbox (plan §3.6, PR 11).

El saludo por hora, el bloque "hora de Bogotá" y el "Current Time" que
exoclaw pone en el mensaje del turno leen `datetime.now()`. En el sandbox
tienen que ver la hora del turno REAL (un saludo de la mañana simulado de
noche cambia la respuesta). `frozen_clock` reemplaza `datetime` en esos
módulos por uno que arranca a la hora del turno y avanza con el reloj real.

Solo esos módulos: el reloj del proceso (`time.time`) queda intacto porque de
él dependen el SDK de Temporal (timeouts, heartbeats) y el propio sandbox.
Las marcas de tiempo que las tools escriben en la metadata del sandbox
quedan con la hora de la corrida (no cambian la respuesta del turno).
"""
from __future__ import annotations

import importlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, tzinfo

FROZEN_MODULES = (
    "exoclaw_conversation.context",
    "src.plugins.chats.agent.sales.context",
    "src.plugins.chats.agent.sales.first_contact_greeting",
)


def _frozen_datetime(at_ms: int) -> type[datetime]:
    wall0 = time.time()

    class TurnDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
            return datetime.fromtimestamp(at_ms / 1000 + (time.time() - wall0), tz)

    return TurnDatetime


@contextmanager
def frozen_clock(at_ms: int) -> Iterator[None]:
    fake = _frozen_datetime(at_ms)
    saved = []
    for name in FROZEN_MODULES:
        module = importlib.import_module(name)
        saved.append((module, module.datetime))
        module.datetime = fake
    try:
        yield
    finally:
        for module, original in saved:
            module.datetime = original
