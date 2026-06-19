"""El **runtime durable** — port + vendors. Como el ConnectorKit, pero para la
EJECUCIÓN: el agente corre detrás de un port, con dos vendors intercambiables:

- `LocalRuntime`     — in-process, determinista, para dev/tests (simula crash y
                       recovery por `execution-id`, sin servidor).
- `AgentSpanRuntime` — el real (server Conductor); el binding se cierra al
                       integrar (G1+). Mismo port → drop-in.

Una `Execution` tiene `id` (execution-id), `status` y `output`. `resume(id)`
recupera una ejecución que quedó a medias: la prueba de durabilidad sin servidor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol, runtime_checkable

Status = Literal["running", "completed", "failed"]
Agent = Callable[[Any], Any]


@dataclass
class Execution:
    id: str
    status: Status
    output: Any = None
    error: str | None = None


@runtime_checkable
class Runtime(Protocol):
    def run(self, agent: Agent, input: Any) -> Execution: ...
    def get(self, execution_id: str) -> Execution: ...
    def resume(self, execution_id: str) -> Execution: ...


class LocalRuntime:
    """Runtime durable in-process (dev/tests). El `execution-id` es un contador
    determinista — NUNCA time/random: si no, el recovery/replay flakea (ver L-1).
    """

    def __init__(self) -> None:
        self._store: dict[str, Execution] = {}
        self._pending: dict[str, tuple[Agent, Any]] = {}
        self._seq = 0

    def _new_id(self) -> str:
        self._seq += 1
        return f"local-{self._seq:06d}"

    def run(self, agent: Agent, input: Any) -> Execution:
        eid = self._new_id()
        self._store[eid] = Execution(id=eid, status="running")
        self._pending[eid] = (agent, input)
        return self._advance(eid)

    def _advance(self, eid: str) -> Execution:
        agent, input = self._pending[eid]
        try:
            out = agent(input)
            self._store[eid] = Execution(id=eid, status="completed", output=out)
        except Exception as e:  # noqa: BLE001
            self._store[eid] = Execution(id=eid, status="failed", error=str(e))
        self._pending.pop(eid, None)
        return self._store[eid]

    def get(self, execution_id: str) -> Execution:
        return self._store[execution_id]

    def resume(self, execution_id: str) -> Execution:
        if execution_id in self._pending:  # quedó a medias -> recupera del input persistido
            return self._advance(execution_id)
        return self._store[execution_id]

    def start_durable(self, agent: Agent, input: Any) -> str:
        """Arranca y persiste SIN completar (simula un crash antes de terminar).
        Devuelve el execution-id para recuperarlo con `resume()`."""
        eid = self._new_id()
        self._store[eid] = Execution(id=eid, status="running")
        self._pending[eid] = (agent, input)
        return eid


class AgentSpanRuntime:
    """El runtime real (server Conductor). Mismo port que `LocalRuntime`. G1+: el
    binding concreto se cierra al integrar (`agentspan server start` +
    `AgentRuntime().run(agent, input)`)."""

    def __init__(self, server_url: str = "http://localhost:6767") -> None:
        self.server_url = server_url

    def run(self, agent: Agent, input: Any) -> Execution:  # pragma: no cover - integración
        raise NotImplementedError("G1+: cablear a agentspan.AgentRuntime().run(agent, input)")

    def get(self, execution_id: str) -> Execution:  # pragma: no cover
        raise NotImplementedError("G1+")

    def resume(self, execution_id: str) -> Execution:  # pragma: no cover
        raise NotImplementedError("G1+")
