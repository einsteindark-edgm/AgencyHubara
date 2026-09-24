"""Lanzador de la caja del laboratorio: prenderla y darle órdenes por SSM.

Reutiliza `Boto3Launcher` (GraphAgents): mismo plano de control (EC2 + SSM,
instance-id por tag), mismas esperas del arranque frío (agente SSM `Online`
antes del primer comando, reintento de `InvalidInstanceId`). Cambian el tag
(`Role=lab`), el chequeo de "lista" (el cloud-init terminó: existe
`/opt/lab/dispatch.sh` y docker responde) y los comandos: los scripts de la
caja (`infra/compose/lab/`), que son idempotentes por id de corrida.

La API de producción nunca abre una conexión a la caja.
"""
from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from src.platform.graphagents.boto3_launcher import Boto3Launcher, _shell_quote

LAB_INSTANCE_TAG = "lab"
RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")
IMAGE_RE = re.compile(r"^ghcr\.io/[a-z0-9._-]+/[a-z0-9._-]+(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?$")


@runtime_checkable
class LabBoxLauncher(Protocol):
    def start_box(self) -> None: ...

    def dispatch(self, run_id: str, image: str) -> str: ...

    def cancel(self, run_id: str) -> str: ...


def _last_line(stdout: str) -> str:
    lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


class Boto3LabLauncher(Boto3Launcher):
    def __init__(self, *, region: str | None = None) -> None:
        super().__init__(region=region, instance_tag=LAB_INSTANCE_TAG)

    def _wait_ready(self, ssm, instance_id: str) -> None:
        """La caja está lista cuando el cloud-init dejó los scripts y docker
        responde (~3 min máximo tras un arranque frío)."""
        loop = (
            "for i in $(seq 1 60); do "
            "test -x /opt/lab/dispatch.sh && docker info >/dev/null 2>&1 && { echo READY; exit 0; }; "
            "sleep 3; done; echo NOTREADY; exit 1"
        )
        self._send(ssm, instance_id, loop)

    def dispatch(self, run_id: str, image: str) -> str:  # type: ignore[override]
        if not RUN_ID_RE.match(run_id):
            raise ValueError(f"id de corrida inválido: {run_id!r}")
        if not IMAGE_RE.match(image):
            raise ValueError(f"imagen inválida: {image!r}")
        ec2, ssm = self._clients()
        stdout = self._send(
            ssm, self._instance_id(ec2), f"/opt/lab/dispatch.sh {_shell_quote(run_id)} {_shell_quote(image)}"
        )
        return _last_line(stdout)

    def cancel(self, run_id: str) -> str:
        if not RUN_ID_RE.match(run_id):
            raise ValueError(f"id de corrida inválido: {run_id!r}")
        ec2, ssm = self._clients()
        return _last_line(self._send(ssm, self._instance_id(ec2), f"/opt/lab/cancel.sh {_shell_quote(run_id)}"))
