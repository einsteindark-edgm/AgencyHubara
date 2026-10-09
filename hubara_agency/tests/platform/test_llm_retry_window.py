"""El turno sobrevive al reinicio de LiteLLM de cada deploy (premortem 2026-10-09).

`backend-deploy.yml` recrea el contenedor de LiteLLM mientras el worker de
ventas sigue atendiendo: 10 a 30 s sin LLM. Con 3 intentos y 2 s de espera
inicial (unos 6 s), un turno que caía ahí agotaba los reintentos de
`llm_chat`, el workflow fallaba y el mensaje del cliente se perdía sin
respuesta ni alerta. Los reintentos son opciones de la activity: cambiarlos
no altera la historia (replay seguro).
"""
from __future__ import annotations

from datetime import timedelta

from src.platform.temporal.retry_policies import _LLM_OPTIONS


def _waits(policy) -> list[float]:
    """Las esperas entre intentos que hace Temporal con esta política."""
    waits: list[float] = []
    interval = policy.initial_interval.total_seconds()
    cap = policy.maximum_interval.total_seconds() if policy.maximum_interval else float("inf")
    for _ in range(policy.maximum_attempts - 1):
        waits.append(min(interval, cap))
        interval *= policy.backoff_coefficient
    return waits


def test_llm_calls_wait_out_a_litellm_restart() -> None:
    assert sum(_waits(_LLM_OPTIONS["retry_policy"])) >= 30


def test_a_single_wait_stays_short() -> None:
    """Esperar no es colgar el turno: ninguna espera pasa de 20 s."""
    policy = _LLM_OPTIONS["retry_policy"]
    assert policy.maximum_interval is not None and policy.maximum_interval <= timedelta(seconds=20)
