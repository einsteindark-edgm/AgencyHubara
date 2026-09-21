"""Escalera de reactivación — cuándo toca el próximo toque proactivo.

Decisión del operador (2026-09-18, análisis de los runs `01a0b0da`…`01a0b586`):
por CADA ghosting —también cuando un remarketing revivió la charla y volvió a
morir— el cliente recibe hasta 5 toques con huecos 2h/2h/4h/6h/6h (a +2h, +4h,
+8h, +14h y +20h del último inbound si nada los atrasa). Tras el quinto sin
respuesta la escalera se AGOTA: no se envía más ni se gasta más.

Por qué existe: antes la dormancia era un piso único desde `last_inbound` y el
tope un `cadence_cap` que contaba outbounds. Una abstención del LLM
(`NO_MESSAGE`) no dejaba outbound → ningún rastro → el ciclo re-despachaba la
misma sesión cada 45 min (86% de los RemarketingWorkflow morían en <30s). Acá
TODO intento —enviado o abstenido— consume su peldaño en
`metadata.remarketing_touches`.

El ancla es `last_inbound_at_ms`: solo cuentan los toques posteriores al último
mensaje del cliente, así que un inbound nuevo reinicia la escalera sola.

Pura (R-DET): sin I/O ni reloj — el caller pasa `now_ms` y persiste el
metadata. Sin `from __future__ import annotations`: `LadderState` puede cruzar
el boundary workflow↔activity (mismo criterio que send_policy.py).
"""
from dataclasses import dataclass
from typing import Any

_MIN = 60 * 1000
_HOUR = 60 * _MIN

#: Huecos entre toques, medidos desde el toque ANTERIOR (el primero, desde el
#: último inbound). Medir desde el anterior —no desde el ancla— hace que un
#: toque atrasado por quiet hours corra a los siguientes en vez de apilarlos.
LADDER_GAPS_MS: tuple[int, ...] = (2 * _HOUR, 2 * _HOUR, 4 * _HOUR, 6 * _HOUR, 6 * _HOUR)

#: 🔥 Gancho transaccional (carrito/pedido/pago pendiente): el primer toque se
#: adelanta a 30 min (carrito abandonado: 30-60 min convierte mejor). NO bajar
#: de 30: silencios cortos suelen ser el cliente pagando o tipeando.
HOT_FIRST_GAP_MS: int = 30 * _MIN

#: Tope de plantillas por cliente en 24h. Meta entrega ~2 plantillas de
#: marketing por usuario al día sumando TODAS las empresas (error 131049);
#: pasarse no entrega y degrada el quality rating del número.
MAX_TEMPLATES_PER_24H: int = 2

#: Tipos de toque registrables.
TOUCH_FREE_FORM = "free_form"
TOUCH_TEMPLATE = "template"
TOUCH_ABSTAINED = "abstained"

#: Cap del log en metadata (el metadata no debe crecer sin límite).
_TOUCH_LOG_MAX = 40
_DAY_MS = 24 * _HOUR


@dataclass(frozen=True)
class LadderState:
    #: toques ya consumidos desde el último inbound (0 = ninguno).
    step: int = 0
    #: el próximo toque ya venció (y la escalera no está agotada).
    due: bool = False
    #: se consumieron todos los peldaños sin respuesta del cliente.
    exhausted: bool = False
    #: cuándo vence el próximo toque (None si agotada o sin ancla).
    next_due_at_ms: int | None = None
    #: ya salieron `MAX_TEMPLATES_PER_24H` plantillas en las últimas 24h.
    template_cap_reached: bool = False
    #: cuándo salió el último toque de esta escalera (None si ninguno).
    last_touch_at_ms: int | None = None


def _touches(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        t
        for t in (metadata.get("remarketing_touches") or [])
        if isinstance(t, dict) and isinstance(t.get("at_ms"), int)
    ]


def ladder_state(
    now_ms: int, metadata: dict[str, Any], *, first_gap_ms: int | None = None
) -> LadderState:
    """Estado de la escalera para una sesión en `now_ms`.

    `first_gap_ms` sobreescribe SOLO el primer hueco: el caller lo deriva del
    calor del lead (🔥 `HOT_FIRST_GAP_MS` con gancho transaccional; ❄️ 4h sin
    ninguna señal). Los huecos siguientes son siempre los de la escalera.
    """
    all_touches = _touches(metadata)
    template_cap = (
        sum(
            1
            for t in all_touches
            if t.get("kind") == TOUCH_TEMPLATE and t["at_ms"] > now_ms - _DAY_MS
        )
        >= MAX_TEMPLATES_PER_24H
    )

    anchor = metadata.get("last_inbound_at_ms")
    if not isinstance(anchor, int):
        # Nunca escribió: no hay ghosting que perseguir con la escalera.
        return LadderState(template_cap_reached=template_cap)

    since_anchor = [t for t in all_touches if t["at_ms"] > anchor]
    step = len(since_anchor)
    last_touch = since_anchor[-1]["at_ms"] if since_anchor else None
    if step >= len(LADDER_GAPS_MS):
        return LadderState(
            step=step,
            exhausted=True,
            template_cap_reached=template_cap,
            last_touch_at_ms=last_touch,
        )

    gap = LADDER_GAPS_MS[step]
    if step == 0 and first_gap_ms is not None:
        gap = first_gap_ms
    next_due = (last_touch if last_touch is not None else anchor) + gap
    return LadderState(
        step=step,
        due=now_ms >= next_due,
        next_due_at_ms=next_due,
        template_cap_reached=template_cap,
        last_touch_at_ms=last_touch,
    )


def record_touch(metadata: dict[str, Any], now_ms: int, kind: str) -> None:
    """Registra un toque (enviado o abstenido) — muta `metadata` in place."""
    touches = _touches(metadata)
    touches.append({"at_ms": now_ms, "kind": kind})
    metadata["remarketing_touches"] = touches[-_TOUCH_LOG_MAX:]
