"""Quiet hours por timezone del cliente — helpers compartidos (platform).

Promovidos desde `plugins/chats/.../watchdog_activities.py` (WS-B2, plan
Window Strategist): la MISMA lógica la necesitan el watchdog (nudge) y el
gate `check_reengagement_policy_activity` del remarketing. Platform no puede
importar de plugins (R-DIP), así que la fuente única vive acá y el watchdog
la re-importa.

Env vars compartidos (deliberado — una sola política de horario):
`WATCHDOG_QUIET_HOURS_START` / `WATCHDOG_QUIET_HOURS_END` (hora local, 24h;
allowed = start <= hora < end; default 08:00-22:00).
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.platform.constants import WHATSAPP_SESSION_PREFIX

#: Hora de inicio del horario permitido (hora local del cliente, 24h format).
DEFAULT_QUIET_HOURS_START: int = 8

#: Hora de fin del horario permitido (exclusive).
DEFAULT_QUIET_HOURS_END: int = 22

#: País (prefijo E.164) → IANA timezone. Longest-match. Extender acá cuando se
#: onboardee un mercado nuevo. Default fallback es UTC (conservador — si no
#: sabemos timezone, no presumimos hora local).
COUNTRY_CODE_TO_TZ: dict[str, str] = {
    "57": "America/Bogota",          # Colombia
    "54": "America/Argentina/Buenos_Aires",  # Argentina
    "52": "America/Mexico_City",     # México
    "56": "America/Santiago",        # Chile
    "51": "America/Lima",            # Perú
    "55": "America/Sao_Paulo",       # Brasil
    "1": "America/New_York",         # USA / Canada (genérico; conservador)
}


def resolve_local_timezone(session_id: str) -> ZoneInfo:
    """Mapea el session_id (`wa_<+code><phone>`) a su IANA timezone.

    Heurística: los primeros 1-3 dígitos del número (post-`+`) son el country
    code; longest-match contra `COUNTRY_CODE_TO_TZ`. Sin match → UTC.
    """
    if not session_id.startswith(WHATSAPP_SESSION_PREFIX):
        return ZoneInfo("UTC")
    phone = session_id[len(WHATSAPP_SESSION_PREFIX):].lstrip("+")
    for code_len in (3, 2, 1):
        tz_name = COUNTRY_CODE_TO_TZ.get(phone[:code_len])
        if tz_name:
            try:
                return ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                continue
    return ZoneInfo("UTC")


def is_quiet_hours_for_session(session_id: str, now_utc: datetime) -> bool:
    """True si la hora LOCAL del cliente está fuera del horario permitido."""
    tz = resolve_local_timezone(session_id)
    local_hour = now_utc.astimezone(tz).hour
    start = int(
        os.environ.get("WATCHDOG_QUIET_HOURS_START", DEFAULT_QUIET_HOURS_START)
    )
    end = int(os.environ.get("WATCHDOG_QUIET_HOURS_END", DEFAULT_QUIET_HOURS_END))
    return not (start <= local_hour < end)
