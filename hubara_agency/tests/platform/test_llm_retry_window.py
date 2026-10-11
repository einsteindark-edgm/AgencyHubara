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


def test_a_hung_llm_does_not_hold_the_turn_longer_than_before() -> None:
    """Revisión de compuertas (2026-10-09): con 6 intentos de hasta 5 min, un
    LiteLLM colgado (que no rechaza: no contesta) trababa el turno unos 30 min;
    con 3 intentos eran unos 15. El tope total queda en 15 min y deja entrar
    al menos dos intentos completos más las esperas."""
    total = _LLM_OPTIONS.get("schedule_to_close_timeout")
    attempt = _LLM_OPTIONS["start_to_close_timeout"]

    assert total is not None and total <= timedelta(minutes=15)
    assert total >= 2 * attempt + timedelta(seconds=sum(_waits(_LLM_OPTIONS["retry_policy"])))
