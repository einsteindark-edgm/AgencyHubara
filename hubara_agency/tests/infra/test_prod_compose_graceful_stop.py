"""El compose de prod deja que los workers se apaguen en orden (auditoría de
Temporal, 2026-10-15; premortem 2026-10-16).

`docker stop` (cada deploy) manda SIGTERM y, pasado `stop_grace_period`
(10 s por defecto), SIGKILL. La imagen corre `python` como PID 1, y el kernel
no le entrega a PID 1 las señales que no maneja: antes de la Ola 0 el SIGTERM
se perdía y el worker moría por SIGKILL a mitad de sus activities. Ahora
`run_worker` instala su handler (PID 1 con handler sí recibe la señal) y espera
a las activities en curso `WORKER_GRACEFUL_SHUTDOWN`; el `stop_grace_period`
tiene que ser mayor.

SIN `init: true` (premortem): el monitoreo de CloudWatch identifica cada
proceso por su línea de comando (`chats\.workers\.sales$`), y con tini la de
PID 1 pasa a ser `docker-init -- python -m …`, que termina igual: dos procesos
por patrón y la memoria por worker promediada a la mitad.
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
def test_every_app_container_gets_time_to_drain_without_an_init(name: str) -> None:
    svc = _app_services()[name]
    assert svc.get("init") is not True, (
        "con `init: true` los patrones de procstat de CloudWatch también matchean a tini "
        "(`docker-init -- python -m …`) y la memoria por worker se promedia a la mitad"
    )
    grace = _seconds(svc.get("stop_grace_period", "10s"))
    assert grace >= WORKER_GRACEFUL_SHUTDOWN.total_seconds() + 10, (
        f"stop_grace_period={grace}s: SIGKILL corta el apagado ordenado ({WORKER_GRACEFUL_SHUTDOWN})"
    )


# Los manifests de k8s no son prod (la caja corre el compose), pero si alguien
# los usa: su default de 30 s coincide con el drenado y el SIGKILL corta el
# apagado ordenado justo cuando el SDK iba a cancelar (premortem 2026-10-16).
_K8S = _PROD_COMPOSE.parents[2] / "hubara_agency" / "k8s" / "aws-produccion"


def _app_deployments() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(_K8S.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not isinstance(doc, dict) or doc.get("kind") != "Deployment":
                continue
            pod = doc["spec"]["template"]["spec"]
            if any(str(c.get("image", "")).startswith("hubara-agency-prod") for c in pod.get("containers", [])):
                out[f"{path.name}:{doc['metadata']['name']}"] = pod
    return out


def test_the_k8s_guard_sees_the_workers() -> None:
    assert len(_app_deployments()) >= 10, sorted(_app_deployments())


@pytest.mark.parametrize("name", sorted(_app_deployments()))
def test_every_k8s_app_pod_gets_time_to_drain(name: str) -> None:
    grace = _app_deployments()[name].get("terminationGracePeriodSeconds", 30)
    assert grace >= WORKER_GRACEFUL_SHUTDOWN.total_seconds() + 10, f"{name}: terminationGracePeriodSeconds={grace}"
