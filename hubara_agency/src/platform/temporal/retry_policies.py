"""Politicas de retry y opciones de timeout centralizadas para los `workflow.execute_activity`.

Tres perfiles canonicos:
  * `_LLM_OPTIONS`: llamadas a LLM (puede tardar, retry conservador).
  * `_TOOL_OPTIONS`: ejecucion de tools (largas, con heartbeat).
  * `_CONV_OPTIONS`: lectura/escritura de conversacion (rapidas, retry agresivo).
"""
from __future__ import annotations

from datetime import timedelta

from temporalio.common import RetryPolicy

#: Premortem 2026-10-09: el deploy recrea LiteLLM mientras el worker atiende
#: (10 a 30 s sin LLM). Con 3 intentos (unos 6 s de espera) el turno agotaba los
#: reintentos, el workflow fallaba y el mensaje del cliente se perdía. Ahora las
#: esperas suman 44 s (2+4+8+15+15) sin que ninguna pase de 15 s. Son opciones
#: de la activity: no cambian la historia (replay seguro).
_LLM_OPTIONS = {
    "start_to_close_timeout": timedelta(minutes=5),
    "retry_policy": RetryPolicy(
        maximum_attempts=6,
        initial_interval=timedelta(seconds=2),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=15),
    ),
}

_TOOL_OPTIONS = {
    "start_to_close_timeout": timedelta(minutes=10),
    "heartbeat_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=1)),
}

_CONV_OPTIONS = {
    "start_to_close_timeout": timedelta(minutes=2),
    "retry_policy": RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1)),
}

#: Perfil público de una activity que llama al LLM (o que arma la sesión que
#: lo va a llamar, como el bootstrap del agente de ventas). Es el MISMO dict
#: que `_LLM_OPTIONS`: lo re-exporta `src.sdk.agentkit` para los workflows de
#: plugins, que no importan platform (P-28). Check:
#: `tests/platform/test_agentkit.py`.
LLM_ACTIVITY_OPTIONS = _LLM_OPTIONS
