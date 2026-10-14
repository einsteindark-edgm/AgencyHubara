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
from datetime import datetime, timedelta, tzinfo
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


def clamp_max_steps(max_steps: int | None) -> int:
    """Peldaños permitidos: `None` = todos; nunca menos de 0 ni más de la escalera."""
    if max_steps is None:
        return len(LADDER_GAPS_MS)
    return max(0, min(int(max_steps), len(LADDER_GAPS_MS)))


def ladder_state(
    now_ms: int,
    metadata: dict[str, Any],
    *,
    first_gap_ms: int | None = None,
    max_steps: int | None = None,
) -> LadderState:
    """Estado de la escalera para una sesión en `now_ms`.

    `first_gap_ms` sobreescribe SOLO el primer hueco: el caller lo deriva del
    calor del lead (🔥 `HOT_FIRST_GAP_MS` con gancho transaccional; ❄️ 4h sin
    ninguna señal). Los huecos siguientes son siempre los de la escalera.

    `max_steps` es cuántos toques como máximo permite el negocio (dashboard
    Agents → Remarketing → Frecuencia; lo lee el caller, esta función sigue
    pura). Usa los primeros N huecos de la escalera. Aplica desde ya: quien ya
    recibió N o más toques queda agotado. `None` = todos los peldaños.
    """
    limit = clamp_max_steps(max_steps)
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
        return LadderState(exhausted=limit == 0, template_cap_reached=template_cap)

    since_anchor = [t for t in all_touches if t["at_ms"] > anchor]
    step = len(since_anchor)
    last_touch = since_anchor[-1]["at_ms"] if since_anchor else None
    if step >= limit:
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
    # El toque anterior pudo agendar el siguiente (análisis 2026-10-13: el 2º
    # toque del que se calló tras el catálogo va a la mañana siguiente).
    scheduled = since_anchor[-1].get("next_at_ms") if since_anchor else None
    if isinstance(scheduled, int):
        next_due = scheduled
    return LadderState(
        step=step,
        due=now_ms >= next_due,
        next_due_at_ms=next_due,
        template_cap_reached=template_cap,
        last_touch_at_ms=last_touch,
    )


#: «La mañana siguiente» (decisión del operador 2026-10-13): el 2º toque al
#: cliente que se calló tras el catálogo. A +2h del 1º respondía el 4 %.
#: Es el valor que el workflow le pasa a `record_remarketing_touch_activity`.
NEXT_TOUCH_MORNING = "manana_siguiente"
NEXT_MORNING_HOUR = 9
#: La mañana arranca con el horario permitido (quiet hours terminan a las
#: 8:00): adelantar el toque para no salir de la ventana, nunca antes de esto.
EARLIEST_MORNING = (8, 15)
#: Sin mañana dentro de la ventana (el cliente escribió temprano): la tarde
#: del mismo día, antes de que empiecen las quiet hours (21:30).
EVENING_HOUR = 19
#: Margen antes de que cierre la ventana de 24 h: el ciclo corre cada 45 min,
#: y fuera de la ventana Meta solo acepta plantilla.
WINDOW_MARGIN_MS = 2 * _HOUR


def _at(day: datetime, hour: int, minute: int = 0) -> int:
    return int(day.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp() * 1000)


def next_morning_at_ms(touch_ms: int, window_expires_ms: int, tz: tzinfo) -> int:
    """Cuándo sale el toque agendado para «la mañana siguiente» a `touch_ms`.

    Pura (R-DET): las horas son las locales del cliente (`tz`). A las 9:00 del
    día siguiente si cabe en la ventana de 24 h con su margen; si no cabe pero
    la ventana cierra después de las 8:15, en ese último momento; y si la
    mañana no existe dentro de la ventana, a las 19:00 del mismo día (nunca
    antes del hueco normal desde el toque anterior)."""
    window_end = window_expires_ms - WINDOW_MARGIN_MS
    touched = datetime.fromtimestamp(touch_ms / 1000, tz)
    next_day = touched + timedelta(days=1)
    morning = min(_at(next_day, NEXT_MORNING_HOUR), window_end)
    if morning >= _at(next_day, *EARLIEST_MORNING):
        return morning
    evening = max(_at(touched, EVENING_HOUR), touch_ms + LADDER_GAPS_MS[1])
    return min(evening, window_end)


def record_touch(
    metadata: dict[str, Any], now_ms: int, kind: str, *, next_at_ms: int | None = None
) -> None:
    """Registra un toque (enviado o abstenido) — muta `metadata` in place.

    `next_at_ms`: el toque agenda cuándo sale el siguiente (manda sobre el
    hueco de la escalera; ver `next_morning_at_ms`)."""
    touches = _touches(metadata)
    touch: dict[str, Any] = {"at_ms": now_ms, "kind": kind}
    if next_at_ms is not None:
        touch["next_at_ms"] = next_at_ms
    touches.append(touch)
    metadata["remarketing_touches"] = touches[-_TOUCH_LOG_MAX:]
