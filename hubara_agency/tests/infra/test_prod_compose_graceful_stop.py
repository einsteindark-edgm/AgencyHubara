"""El compose de prod deja que los workers se apaguen en orden (auditoría de
Temporal, 2026-10-15).

`docker stop` (cada deploy) manda SIGTERM y, pasado `stop_grace_period`
(10 s por defecto), SIGKILL. La imagen corre `python` como PID 1, y el kernel
no le entrega a PID 1 las señales que no maneja: sin `init: true` el SIGTERM
se perdía y el worker moría por SIGKILL a mitad de sus activities. Con un init
(tini) la señal llega a `run_worker`, que espera a las activities en curso
`WORKER_GRACEFUL_SHUTDOWN`; el `stop_grace_period` tiene que ser mayor.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.platform.temporal.worker_lifecycle import WORKER_GRACEFUL_SHUTDOWN

_PROD_COMPOSE = Path(__file__).resolve().parents[3] / "infra" / "compose" / "docker-compose.prod.yml"


def _app_services() -> dict[str, dict]:
    services = yaml.safe_load(_PROD_COMPOSE.read_text(encoding="utf-8"))["services"]
    return {name: svc for name, svc in services.items() if svc.get("image") == "${HUBARA_IMAGE}"}


def _seconds(value: str) -> float:
    m = re.fullmatch(r"(\d+)(s|m)", str(value))
    assert m, f"stop_grace_period ilegible: {value!r}"
    return int(m.group(1)) * (60 if m.group(2) == "m" else 1)


def test_the_guard_sees_the_workers_and_the_api() -> None:
    names = set(_app_services())
    assert {"api", "worker-chats-sales", "worker-eta-eta"} <= names
    assert sum(n.startswith("worker-") for n in names) >= 10


@pytest.mark.parametrize("name", sorted(_app_services()))
def test_every_app_container_gets_sigterm_and_time_to_drain(name: str) -> None:
    svc = _app_services()[name]
    assert svc.get("init") is True, "python corre como PID 1: sin `init: true` el SIGTERM no le llega"
    grace = _seconds(svc.get("stop_grace_period", "10s"))
    assert grace >= WORKER_GRACEFUL_SHUTDOWN.total_seconds() + 10, (
        f"stop_grace_period={grace}s: SIGKILL corta el apagado ordenado ({WORKER_GRACEFUL_SHUTDOWN})"
    )
