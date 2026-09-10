"""Día calendario en hora de Colombia (America/Bogota).

El dashboard lo opera gente en Colombia y las fechas que maneja
(`hubara_scheduled_delivery_iso`, los filtros "Para hoy"/"Mañana") son días
calendario que el operador elige a mano, no instantes. Cortar esos días en UTC
adelanta la frontera a las 19:00 hora local: entre las 7 de la tarde y la
medianoche "hoy" ya devuelve mañana, y toda entrega agendada para el día en
curso que aún no salió se marcaba `overdue`.

Nota de duplicación: `plugins/ads/aggregation.py` y
`plugins/chats/agent/sales/context.py` traen cada uno su propia resolución de
la zona (offset fijo y ZoneInfo respectivamente), ambas anteriores a este
módulo. No se unificaron acá para no arrastrar dos dominios a un cambio de
Órdenes; este módulo es el destino natural cuando se toquen.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)


def _resolve_bogota_tz() -> ZoneInfo | timezone:
    """Carga la zona de Bogotá con fallback defensivo.

    En containers minimalistas (alpine sin `tzdata`) `ZoneInfo` lanza
    `ZoneInfoNotFoundError`. Se cae a UTC-5 fijo: Colombia no tiene horario de
    verano desde 1993, así que el offset es estable todo el año. El warning
    existe para que la operación se entere si algún día eso deja de ser cierto.
    (Mismo criterio que `plugins/chats/agent/sales/context.py`.)
    """
    try:
        return ZoneInfo("America/Bogota")
    except ZoneInfoNotFoundError:
        log.warning(
            "tzdata para America/Bogota no disponible; usando offset fijo "
            "UTC-5. Instalar tzdata en el container si Colombia adopta DST."
        )
        return timezone(timedelta(hours=-5))


BOGOTA_TZ = _resolve_bogota_tz()


def bogota_day_iso(instant: datetime | None = None) -> str:
    """Día calendario YYYY-MM-DD en Colombia del instante dado.

    Puro respecto al `instant` que recibe; sólo el default consulta el reloj.
    Esa inyección es lo que hace testeable el borde de las 19:00 — sin ella no
    habría forma determinista de fijarlo (el proyecto no usa freezegun).

    Un `instant` naive se interpreta como UTC: el backend mezcla datetimes
    aware y naive, y asumir la zona del host sería peor que asumir UTC.
    """
    moment = instant or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(BOGOTA_TZ).date().isoformat()


def shift_iso_day(iso: str, days: int) -> str:
    """Aritmética de día calendario sobre YYYY-MM-DD, sin tocar zonas.

    El input ya es un día (no un instante), así que sumarle días es aritmética
    de calendario pura — meter la zona acá aplicaría el offset dos veces.
    """
    return (
        datetime.strptime(iso, "%Y-%m-%d").date() + timedelta(days=days)
    ).isoformat()
