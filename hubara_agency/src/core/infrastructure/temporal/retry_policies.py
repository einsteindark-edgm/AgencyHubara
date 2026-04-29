"""Politicas de retry y opciones de timeout centralizadas para los `workflow.execute_activity`.

Tres perfiles canonicos:
  * `_LLM_OPTIONS`: llamadas a LLM (puede tardar, retry conservador).
  * `_TOOL_OPTIONS`: ejecucion de tools (largas, con heartbeat).
  * `_CONV_OPTIONS`: lectura/escritura de conversacion (rapidas, retry agresivo).
"""
from __future__ import annotations

from datetime import timedelta

from temporalio.common import RetryPolicy

_LLM_OPTIONS = {
    "start_to_close_timeout": timedelta(minutes=5),
    "retry_policy": RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)),
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
