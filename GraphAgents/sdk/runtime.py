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

Status = Literal["running", "completed", "failed", "paused"]
Agent = Callable[[Any], Any]


@dataclass
class AwaitingHuman:
    """Sentinel que un agente puro DEVUELVE para pedir una decisión humana.

    El runtime lo mapea a `Execution(status="paused", awaiting=context)`. En el
    grafo real, `AgentSpanRuntime` mapea el `interrupt()` de LangGraph al MISMO
    contrato — el primitivo HITL es el contrato del port, no el mecanismo de
    cada vendor.
    """

    context: Any


@dataclass
class Execution:
    id: str
    status: Status
    output: Any = None
    error: str | None = None
    awaiting: Any = None  # contexto de la decisión pendiente (sólo si status=="paused")


@runtime_checkable
class Runtime(Protocol):
    def run(self, agent: Agent, input: Any) -> Execution: ...
    def get(self, execution_id: str) -> Execution: ...
    def resume(self, execution_id: str, decision: Any = None) -> Execution: ...


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

    def _advance(self, eid: str, decision: Any = None) -> Execution:
        agent, input = self._pending[eid]
        try:
            out = agent(input, decision=decision) if decision is not None else agent(input)
        except Exception as e:  # noqa: BLE001
            self._store[eid] = Execution(id=eid, status="failed", error=str(e))
            self._pending.pop(eid, None)
            return self._store[eid]
        if isinstance(out, AwaitingHuman):  # pide humano -> pausa durable (sigue pendiente)
            self._store[eid] = Execution(id=eid, status="paused", awaiting=out.context)
            return self._store[eid]
        self._store[eid] = Execution(id=eid, status="completed", output=out)
        self._pending.pop(eid, None)
        return self._store[eid]

    def get(self, execution_id: str) -> Execution:
        return self._store[execution_id]

    def resume(self, execution_id: str, decision: Any = None) -> Execution:
        if execution_id in self._pending:  # a medias (crash) o en pausa HITL -> retoma
            return self._advance(execution_id, decision=decision)
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

    def start(self, agent: Any, input: Any) -> str:
        """Submit NO-BLOQUEANTE: `AgentRuntime.start()` devuelve un handle con el execution-id en
        ~0.4s (NO espera a que el workflow complete); los workers corren en background. Un thread
        daemon mantiene el runtime vivo (los workers polleando Conductor) hasta que el workflow
        termina, y ahí lo cierra. Es lo que deja al viewer ver los nodos activarse EN VIVO
        (pending→running→done): el caller pollea el trace/`get_status` mientras corre. Devuelve el
        execution-id. NO uses esto para tests que necesitan el output — para eso está `run()`."""
        import threading
        import time as _time

        rt = self._client()  # SIN context manager: vive hasta que el drain lo cierre
        handle = rt.start(agent, input)
        eid = handle.execution_id

        def _drain() -> None:
            try:  # los workers corren solos; este loop solo sabe CUÁNDO cerrar el runtime
                # techo ~600s (el pod real tarda ~1.4s): reapa los workers LOCALES si el workflow
                # cuelga — el workflow server-side quedaría stalled, pero no fuga procesos (hoy
                # teórico: el catálogo es grafos puros de ~1.4s).
                for _ in range(1200):
                    if getattr(rt.get_status(eid), "is_complete", False):
                        break
                    _time.sleep(0.5)
            except Exception:  # noqa: BLE001 — el drain NUNCA debe tumbar el server
                pass
            finally:
                try:
                    rt.shutdown()  # reap de los worker processes
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=_drain, daemon=True).start()
        return eid

    def get(self, execution_id: str) -> Execution:
        with self._client() as rt:
            st = rt.get_status(execution_id)
        if getattr(st, "is_complete", False):
            status: Status = "completed"
        elif getattr(st, "is_waiting", False):  # HUMAN task pendiente (HITL) → pausa durable
            status = "paused"
        else:
            status = "running"
        return Execution(id=execution_id, status=status, output=self._unwrap(getattr(st, "output", None)))

    def resume(self, execution_id: str, decision: Any = None) -> Execution:
        """HITL: completa la HUMAN task pendiente con la decisión (vía `respond`),
        que fluye al state como `{"decision": ...}` (la key del nodo `await_approval`)
        → el grafo corre `apply` y termina. Sin decisión = no-op (devuelve el estado
        actual). Validado en vivo contra Conductor (`:6767`)."""
        if decision is not None:
            with self._client() as rt:
                rt.respond(execution_id, {"decision": decision})
        return self.get(execution_id)
