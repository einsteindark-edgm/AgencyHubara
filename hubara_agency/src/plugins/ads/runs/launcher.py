"""Launcher port — la interfaz que el buzón usa para hablar con la caja GraphAgents:
despertarla, despachar un run, y resumir un HITL.

Es un side-effect de red (EC2 + SSM) detrás de un port (DEHA R-DIP): el orchestrator
depende de ESTA interfaz, no de boto3. Vendors: `FakeLauncher` (tests, en
`tests/plugins/ads/`) y `Boto3Launcher` (real — `ec2:StartInstances` +
`ssm:SendCommand` scoped al tag `Role=graphagents`; import perezoso + NO-OP sin config).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Launcher(Protocol):
    def start_box(self) -> None:
        """Despierta la caja GraphAgents y espera a que AgentSpan esté healthy. Idempotente
        (si ya está corriendo, no-op)."""
        ...

    def dispatch(self, agent: str, input: dict, *, run_id: str) -> str:
        """Despacha un run a AgentSpan por SSM (`sdk.cli start <agent> --input ...`).
        Devuelve el `execution-id` de Conductor (para pollearlo)."""
        ...

    def resume(self, execution_id: str, decision: dict) -> None:
        """Completa la HUMAN task pendiente con la decisión (SSM `sdk.cli resume <eid>
        --decision ...`). Despierta la caja si está dormida."""
        ...

    def conductor_base_url(self) -> str:
        """La URL REST de Conductor de la caja (su IP privada ACTUAL + `:6767`). Se resuelve
        FRESCA cada vez — la IP es DINÁMICA (autostop) — y el poller del buzón la usa para
        relayar el progreso. NUNCA hardcodear ni cachear."""
        ...
