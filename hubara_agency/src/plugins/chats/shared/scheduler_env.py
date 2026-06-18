"""Overrides por env de las cadencias de schedulers del plugin ``chats`` (ACK-4).

Centraliza el NOMBRE de cada env var + el parseo, para que los call sites que
comparten una cadencia no diverjan en el nombre ni en la semántica. Caso
paradigmático: el pre-expiry del watchdog se usa en DOS lugares — el use_case de
sales que agenda el `fire_at` (``ingest_inbound_message``) y la activity de
remarketing que re-chequea la ventana (``watchdog_activities``). Si cada uno
leyera ``os.environ["WATCHDOG_PRE_EXPIRY_MS"]`` por su cuenta, un typo en uno
desincronizaría agendado vs. re-check sin que ningún test lo note.

El DEFAULT lo provee el caller (la constante PURA de ``platform/whatsapp/window.py``
o ``EvalWindowInput``): así este módulo NO importa ``src.platform`` (no toca el
ratchet P-28) y ``window.py`` sigue 100% puro (R-DET). Leer env acá es legítimo
porque los callers son activities / use_cases, nunca workflows.
"""
from __future__ import annotations

import os

#: Pre-expiry del watchdog (cuánto antes del cierre de la ventana dispara).
WATCHDOG_PRE_EXPIRY_MS_ENV = "WATCHDOG_PRE_EXPIRY_MS"

#: Duración de la ventana de servicio de WhatsApp (24h por default).
SERVICE_WINDOW_MS_ENV = "WHATSAPP_SERVICE_WINDOW_MS"

#: Mínimo de turnos para que un episodio se evalúe (debajo → trivial, skip).
SALES_EVAL_MIN_TURNS_ENV = "SALES_EVAL_MIN_TURNS"

#: Umbral de score promedio bajo el cual un episodio es candidato a golden.
SALES_EVAL_CANDIDATE_THRESHOLD_ENV = "SALES_EVAL_CANDIDATE_THRESHOLD"


def env_int(name: str, default: int) -> int:
    """Lee ``name`` como int. Vacío/ausente → ``default``. Inválido → ValueError
    (una mala config debe romper ruidosamente, no degradar en silencio)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def env_float(name: str, default: float) -> float:
    """Lee ``name`` como float. Vacío/ausente → ``default``. Inválido → ValueError."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)
