"""Cuándo un workflow de sesión hace continue-as-new (auditoría de Temporal, 2026-10-15).

El tope cuenta los turnos DEL RUN. El run que nace de un continue-as-new trae el
contador acumulado (las claves de los turnos lo usan); compararlo con el tope
hacía que, desde el turno 50, cada turno hiciera continue-as-new y repitiera el
bootstrap. Segundo disparador: el servidor sugiere continue-as-new cuando la
historia se acerca a sus límites (eventos o bytes) antes del tope de turnos.

Replay (L-9): las dos reglas solo difieren en dónde cortan. Un run vivo nunca
cortó con la vieja (si no, ya no viviría), y donde la vieja no corta la nueva
tampoco, salvo por la sugerencia del servidor. El gate se consulta SOLO donde
difieren: los runs vivos al desplegar no lo encuentran en su historia y pasan a
la regla nueva en su próximo turno (sin la memoización de `patched` en False),
y la historia de un run que ya cortó con la vieja se reproduce igual.
Puro y determinista: se importa en el sandbox del workflow.
"""
from __future__ import annotations

from temporalio import workflow

#: El gate de la regla nueva (sesiones de ventas V1 y V2, y ETA).
CONTINUE_AS_NEW_PATCH = "continue-as-new-per-run-v1"


def continue_as_new_due(*, run_turns: int, total_turns: int, limit: int) -> bool:
    """¿Corta este run? `run_turns`: turnos de este run; `total_turns`: el
    contador acumulado (la regla vieja). El caller ya verificó que no hay
    mensajes pendientes."""
    legacy = total_turns >= limit
    due = run_turns >= limit or workflow.info().is_continue_as_new_suggested()
    if due == legacy:
        return due
    return due if workflow.patched(CONTINUE_AS_NEW_PATCH) else legacy
