"""Quiet hours por timezone del cliente — helpers compartidos (platform).

Promovidos desde `plugins/chats/.../watchdog_activities.py` (WS-B2, plan
Window Strategist): la MISMA lógica la necesitan el watchdog (nudge) y el
gate `check_reengagement_policy_activity` del remarketing. Platform no puede
importar de plugins (R-DIP), así que la fuente única vive acá y el watchdog
la re-importa.

Env vars compartidos (deliberado — una sola política de horario):
`WATCHDOG_QUIET_HOURS_START` / `WATCHDOG_QUIET_HOURS_END` (hora local, 24h,
acepta `HH` o `HH:MM`; allowed = start <= hora < end; default 08:00-21:30).
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.platform.constants import WHATSAPP_SESSION_PREFIX

#: Inicio del horario permitido (hora local del cliente, `HH` o `HH:MM`).
DEFAULT_QUIET_HOURS_START: str = "8"

#: Fin del horario permitido (exclusive). 21:30 y no 22:00 — política del
#: operador 2026-08-04: pasada esa hora los toques interrumpen y generan
#: bloqueos/reportes que degradan el quality rating del número en Meta.
DEFAULT_QUIET_HOURS_END: str = "21:30"

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


def _parse_hhmm(value: str) -> tuple[int, int]:
    """`"21:30"` → (21, 30); `"22"` → (22, 0) — backwards-compatible."""
    raw = value.strip()
    if ":" in raw:
        hh, mm = raw.split(":", 1)
        return (int(hh), int(mm))
    return (int(raw), 0)


def is_quiet_hours_for_session(session_id: str, now_utc: datetime) -> bool:
    """True si la hora LOCAL del cliente está fuera del horario permitido."""
    tz = resolve_local_timezone(session_id)
    local = now_utc.astimezone(tz)
    local_time = (local.hour, local.minute)
    start = _parse_hhmm(
        os.environ.get("WATCHDOG_QUIET_HOURS_START", DEFAULT_QUIET_HOURS_START)
    )
    end = _parse_hhmm(
        os.environ.get("WATCHDOG_QUIET_HOURS_END", DEFAULT_QUIET_HOURS_END)
    )
    return not (start <= local_time < end)
