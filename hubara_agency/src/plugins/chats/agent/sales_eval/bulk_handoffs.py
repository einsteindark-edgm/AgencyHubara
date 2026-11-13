"""¿Qué datos pidió el equipo después de un relevo al por mayor? (tarjeta 34 del ciclo)

La decisión `datos_por_mayor` (paquete ventas-10) anota, al relevar un pedido
al por mayor, qué datos le faltaban a Colossus. Para saber si acertó, el
informe de estrategia compara con lo que el equipo tuvo que pedir después: Jev
lee lo que escribió el equipo tras el relevo (en la caja, con lo personal ya
tapado) y dice cuál de los cinco datos le pidió al cliente. Al informe solo
llega el conteo.

Es una medición del evaluador, no una decisión del bot: no está en el paquete
ni cambia nada en la conversación.
"""
from __future__ import annotations

import os
from typing import Any

#: Los datos que anota `datos_por_mayor` (mismo orden y mismos códigos).
BULK_DATA: tuple[str, ...] = ("producto", "cantidad", "fecha", "ciudad", "empresa")
#: Los relevos que anota (los mismos de `escalate_to_human`; una prueba lo exige).
BULK_HANDOFF_REASONS: tuple[str, ...] = ("BULK_ORDER", "WHOLESALE_B2B", "CORPORATE_EVENT")
_ASKED_YES = 0.7
_TIMEOUT_S = 20.0

_QUESTION_TEXT = {
    "producto": "¿El equipo le preguntó al cliente qué producto quiere (cuál vela, caja o kit)?",
    "cantidad": "¿El equipo le preguntó al cliente cuántas unidades quiere?",
    "fecha": "¿El equipo le preguntó al cliente para cuándo lo necesita (fecha o plazo)?",
    "ciudad": "¿El equipo le preguntó al cliente a qué ciudad o municipio va el pedido?",
    "empresa": "¿El equipo le preguntó al cliente si la compra es para una empresa o negocio?",
}


def _questions() -> list[Any]:
    from src.sdk.connectorkit import TypedQuestion

    return [
        TypedQuestion(
            id=f"equipo_por_mayor.{datum}", kind="noul", text=text,
            criteria={"true": "sí: se lo preguntó", "false": "no se lo preguntó"},
        )
        for datum, text in _QUESTION_TEXT.items()
    ]


def _state(team_text: str) -> str:
    return (
        "Un colega del equipo de una tienda de velas recibió de un asesor un pedido al por mayor, un pedido grande "
        "o un evento de empresa. Esto es lo que el colega le escribió al cliente después (los datos personales "
        f"aparecen tapados):\n\n{team_text.strip()}"
    )


async def jev_team_reader(team_text: str) -> frozenset[str] | None:
    """Los datos que el equipo le pidió al cliente según Jev, o None si Jev no
    contestó todas las preguntas con certeza (no se adivina)."""
    from src.plugins.chats.agent.sales.decisions.bots import DEFAULT_PROFILE
    from src.plugins.chats.agent.sales.decisions.profiles import get_engine_profile
    from src.sdk.connectorkit import get_perception_port

    profile = get_engine_profile((os.getenv("SALES_PERCEPTION_PROFILE") or "").strip() or DEFAULT_PROFILE)
    if profile is None:
        return None
    result = await get_perception_port(profile.oracle).ask(_state(team_text), _questions(), timeout_s=_TIMEOUT_S)
    if not result.ok:
        return None
    asked: set[str] = set()
    for datum in BULK_DATA:
        answer = result.answer(f"equipo_por_mayor.{datum}")
        if answer is None or answer.p is None:
            return None
        if answer.p >= _ASKED_YES:
            asked.add(datum)
        elif answer.p > 1 - _ASKED_YES:
            return None
    return frozenset(asked)


__all__ = ["BULK_DATA", "BULK_HANDOFF_REASONS", "jev_team_reader"]
