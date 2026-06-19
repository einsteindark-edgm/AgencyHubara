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
    """El runtime REAL (server Conductor). Mismo port que `LocalRuntime`. Corre un
    `CompiledStateGraph` (de `loader.build_agent`) sobre el server de AgentSpan vía
    `AgentRuntime().run(graph, input)` y mapea su `AgentResult` a `Execution`.

    AgentSpan envuelve el output del passthrough como `{'result': '<json del state
    final>'}`; lo desempaquetamos para devolver el output real de la capability
    (ver L-8). `server_url=None` → el SDK usa `AGENTSPAN_SERVER_URL` (o el default
    `http://localhost:6767/api`)."""

    def __init__(self, server_url: str | None = None) -> None:
        self.server_url = server_url

    def _client(self):
        from agentspan.agents import AgentRuntime

        return AgentRuntime(server_url=self.server_url) if self.server_url else AgentRuntime()

    @staticmethod
    def _unwrap(output: Any) -> Any:
        """El passthrough devuelve `{'result': '<json del state>'}` → el state."""
        import json

        if isinstance(output, dict) and list(output) == ["result"] and isinstance(output["result"], str):
            try:
                return json.loads(output["result"])
            except (ValueError, TypeError):
                return output
        return output

    def run(self, agent: Any, input: Any) -> Execution:
        with self._client() as rt:
            res = rt.run(agent, input)
        return Execution(
            id=res.execution_id,
            status="completed" if res.is_success else "failed",
            output=self._unwrap(res.output),
            error=getattr(res, "error", None),
        )

    def get(self, execution_id: str) -> Execution:
        with self._client() as rt:
            st = rt.get_status(execution_id)
        status = "completed" if getattr(st, "is_complete", False) else "running"
        return Execution(id=execution_id, status=status, output=self._unwrap(getattr(st, "output", None)))

    def resume(self, execution_id: str) -> Execution:
        # El re-attach real de AgentSpan necesita re-pasar el grafo
        # (`AgentRuntime().resume(eid, graph)`); el port `resume(id)` no lo recibe,
        # así que devolvemos el estado actual. Recovery mid-flight real: G1.x.
        return self.get(execution_id)
