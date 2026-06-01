"""Helper puro para construir el contexto dinámico de turno del agente Sales.

Sin side-effects más allá de leer la hora del sistema. Reutilizable desde
código sincrónico (use cases, scripts) Y desde activities. El propósito
principal es entregar al LLM la hora actual en zona `America/Bogota` y el
saludo apropiado para la franja horaria, de modo que la apertura de cada
sesión nueva use "Buenos días / Buenas tardes / Buenas noches" según la
hora local de Colombia.

Convenciones:
- Tuteo colombiano en el bloque de contexto (consistente con USER.md /
  SOUL.md / SCRIPT.md).
- Sin em dash en el texto inyectado al modelo (puntuación natural).

Este helper NO es una activity. La capa Temporal lo envuelve en
`activities/inject_context.py` cuando se necesita invocar desde un workflow
determinístico. Desde un use case (que ya puede hacer I/O) se llama directo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)


def _resolve_bogota_tz() -> "ZoneInfo | timezone":
    """Carga la TZ de Bogotá con fallback defensivo.

    Premortem FIX #4: en algunos containers minimalistas (`alpine` sin
    `tzdata`) `ZoneInfo("America/Bogota")` lanza `ZoneInfoNotFoundError`.
    Caemos a UTC-5 fijo (Colombia no tiene horario de verano, así que el
    offset es estable todo el año) con warning para alertar la operación.
    """
    try:
        return ZoneInfo("America/Bogota")
    except ZoneInfoNotFoundError:
        log.warning(
            "tzdata para America/Bogota no disponible; usando offset fijo "
            "UTC-5. Considera instalar tzdata en el container para precisión "
            "futura si Colombia adopta DST."
        )
        return timezone(timedelta(hours=-5))


_BOGOTA_TZ = _resolve_bogota_tz()

# Días de la semana en español (lunes=0, ..., domingo=6) — alineado con
# datetime.weekday() que devuelve 0 para lunes.
_WEEKDAYS_ES = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)


def greeting_for_hour(hour: int) -> str:
    """Devuelve el saludo en español colombiano según la franja horaria.

    Reglas (zona `America/Bogota`):
      - 05:00 a 11:59 → "Buenos días"
      - 12:00 a 18:59 → "Buenas tardes"
      - 19:00 a 04:59 → "Buenas noches"

    Esta función es pura. No lee la hora del sistema; el caller le pasa la
    hora ya calculada en TZ Colombia. Útil para tests unitarios sin patcheo
    de datetime.
    """
    if 5 <= hour < 12:
        return "Buenos días"
    if 12 <= hour < 19:
        return "Buenas tardes"
    return "Buenas noches"


def build_bogota_context_string(now: datetime | None = None) -> str:
    """Construye el bloque de contexto dinámico con hora y saludo de Colombia.

    El bloque se inyecta en el system prompt (vía `plugin_context`) cada
    turno donde el cliente mandó un mensaje, para que el LLM use el saludo
    apropiado según la franja horaria local de Colombia y disponga de la
    hora actual sin tener que deducirla del runtime context del servidor.

    Args:
        now: opcional, datetime con tzinfo. Si es None, se lee
            `datetime.now(America/Bogota)`. Permite inyectar tiempo fijo en
            tests.

    Returns:
        Un string multi-línea listo para añadirse como elemento de
        `plugin_context: list[str]` del signal `send_message`.
    """
    if now is None:
        now = datetime.now(_BOGOTA_TZ)
    elif now.tzinfo is None:
        # Defensivo: si llega un datetime naive, lo interpretamos como
        # Bogotá (no UTC) para evitar saludos torcidos en producción.
        now = now.replace(tzinfo=_BOGOTA_TZ)
    else:
        now = now.astimezone(_BOGOTA_TZ)

    weekday = _WEEKDAYS_ES[now.weekday()]
    greeting = greeting_for_hour(now.hour)
    fecha = now.strftime("%d/%m/%Y")
    hora = now.strftime("%H:%M")

    return (
        "[CONTEXTO DE TURNO, metadata, no es instrucción del usuario]\n"
        f"Hora actual en Colombia (America/Bogota): {hora} del {weekday} {fecha}.\n"
        f'Saludo apropiado para esta franja si abres una sesión nueva: "{greeting}".'
    )
