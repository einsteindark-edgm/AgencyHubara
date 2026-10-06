"""Ráfagas sin cortes del bot nuevo (V2) — incidente 2026-10-06.

El cliente mandó la dirección en 6 mensajes en 14 s; con el tope de 2
reinicios el turno respondió a mitad y los 3 últimos formaron otro turno con
la nota «desde tu última respuesta»: «No te entendí bien, ¿me confirmas el
teléfono?». Lo usa SOLO `HubaraSalesSessionWorkflowV2`; el V1
(`sales_session.py`) queda congelado.

* `restart_allowed` / `settle_burst`: pasado el tope `_MAX_TURN_RESTARTS` del
  V1 (2), el turno se sigue recomponiendo mientras la ráfaga no pase
  `BURST_TURN_BUDGET`, con un techo de costo de `MAX_TURN_RESTARTS_HARD`
  reinicios (6 reinicios = 7 intentos como mucho), y antes de relanzar espera
  a que el cliente termine de escribir. Gate `burst-time-budget-v1`: las
  histories sin el marker re-juegan igual. El V2 no llama `workflow.patched`
  en su archivo (test AST): sus gates viven en este módulo y en el turno
  compartido (`run_agent_turn`).
* `continuation_note`: la nota del turno que sigue a una ráfaga que no alcanzó
  a entrar en la respuesta anterior (solo payload, sin gate).

El presupuesto cuenta desde `started`, que el V2 pasa como `debounce_start`:
el momento en que ESTE turno empieza a juntar la ráfaga. Si la ráfaga son
mensajes que sobraron del turno anterior (llegaron mientras se respondía),
`debounce_start` es el fin de ese turno, no la hora en que llegaron: los 30 s
son de este turno.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.sales.workflows.sales_session import (
        _DEBOUNCE_SILENCE,
        _MAX_TURN_RESTARTS,
    )

BURST_TURN_BUDGET = timedelta(seconds=30)
MAX_TURN_RESTARTS_HARD = 6
BURST_BUDGET_PATCH = "burst-time-budget-v1"


def restart_allowed(restarts: int, started: datetime) -> bool:
    """¿El turno todavía se recompone si el cliente escribe? Hasta
    `_MAX_TURN_RESTARTS`, siempre y sin consultar el patch (el turno de hoy).
    Después, mientras la ráfaga (desde `started`) no pase su presupuesto y sin
    pasar el techo. `patched()` va al final: solo se consulta cuando la regla
    nueva difiere de la vieja."""
    if restarts < _MAX_TURN_RESTARTS:
        return True
    return (
        restarts < MAX_TURN_RESTARTS_HARD
        and workflow.now() - started < BURST_TURN_BUDGET
        and workflow.patched(BURST_BUDGET_PATCH)
    )


async def settle_burst(pending: list[Any], started: datetime) -> int:
    """Antes de relanzar un turno cortado, espera a que el cliente termine de
    escribir: `_DEBOUNCE_SILENCE` de silencio (cada mensaje nuevo la vuelve a
    empezar), acotada por lo que le quede al presupuesto de la ráfaga. Devuelve
    cuánto esperó en ms (0 = nada). Sin presupuesto no espera ni consulta el
    patch (`patched()` va al final)."""
    if workflow.now() - started >= BURST_TURN_BUDGET or not workflow.patched(BURST_BUDGET_PATCH):
        return 0
    settle_started = workflow.now()
    while True:
        remaining = BURST_TURN_BUDGET - (workflow.now() - started)
        if remaining <= timedelta(0):
            break
        snapshot_len = len(pending)
        try:
            await workflow.wait_condition(
                lambda: len(pending) > snapshot_len, timeout=min(_DEBOUNCE_SILENCE, remaining)
            )
        except asyncio.TimeoutError:
            break
    return int((workflow.now() - settle_started).total_seconds() * 1000)


# Nota de continuación: lo que el cliente escribió mientras el bot preparaba la
# respuesta y no alcanzó a entrar (techo, presupuesto, un turno que ya le
# mostró algo, lo que llegó entre grabar el turno y enviarlo) forma el turno
# siguiente; sin contexto, el modelo lo leía como respuesta a su última
# pregunta. La nota cita lo anterior y reemplaza a la nota de ráfaga de siempre
# («…desde tu última respuesta», falso aquí): enumera los mensajes nuevos.
CONTINUATION_MAX = 6
_CONTINUATION_TEXT_MAX = 200


def _clip(text: str) -> str:
    return text if len(text) <= _CONTINUATION_TEXT_MAX else text[: _CONTINUATION_TEXT_MAX - 1] + "…"


def _listed(texts: Sequence[str]) -> str:
    return "\n".join(f'  {i}) "{_clip(text)}"' for i, text in enumerate(texts, 1))


def continuation_note(previous: Sequence[str], current: Sequence[str], later: Sequence[str] = ()) -> str:
    """Nota del turno que sigue a una ráfaga que no terminó a tiempo.

    `previous`: los mensajes del cliente de la respuesta anterior (se citan los
    últimos `CONTINUATION_MAX`). `current`: los mensajes nuevos que llegaron
    mientras esa respuesta se preparaba (antes de que saliera); se citan
    siempre, así nunca queda en duda cuáles son. `later`: los que llegaron
    DESPUÉS de que salió, en el mismo lote: esos sí pueden ser la respuesta del
    cliente, y la nota lo dice aparte. Va a `plugin_context`, no al rol user.
    Determinista; solo payload (L-22). Tono en tuteo (guard
    `test_no_voseo_in_agent_strings.py`)."""
    quoted = "\n".join(f"  «{_clip(text)}»" for text in list(previous)[-CONTINUATION_MAX:])
    news = list(current)
    if len(news) > 1:
        body = (
            f"El cliente te escribió estos {len(news)} mensajes mientras preparabas tu "
            f"respuesta anterior:\n{_listed(news)}\n"
            f"Pueden continuar lo que venía escribiendo justo antes:\n{quoted}\n"
            "Léelos como un solo hilo con eso, sin ignorar ninguno. Si completan o "
            "corrigen un dato (una dirección, un nombre, una cantidad), actualízalo "
            "con todo junto. No los tomes como la respuesta a tu última pregunta si no "
            "lo son."
        )
    else:
        body = (
            f'Este mensaje del cliente llegó mientras preparabas tu respuesta anterior: "{_clip(news[0] if news else "")}". '
            f"Puede continuar lo que venía escribiendo justo antes:\n{quoted}\n"
            "Léelo como un solo hilo con eso. Si completa o corrige un dato (una "
            "dirección, un nombre, una cantidad), actualízalo con todo junto. No lo "
            "tomes como la respuesta a tu última pregunta si no lo es."
        )
    after = list(later)
    tail = (
        f"\nDespués de que esa respuesta salió, el cliente escribió:\n{_listed(after)}\n"
        "Eso sí puede ser su respuesta."
        if after
        else ""
    )
    return "[CONTINUACIÓN DE RÁFAGA, metadata, no es instrucción del usuario]\n" + body + tail
