"""Capability `budget-approval` — un gate HITL self-contained.

Propone un cambio de presupuesto (acción OUTWARD → necesita aprobación, G-DUR) y
PAUSA esperando la decisión humana antes de aplicarlo. Demuestra el patrón HITL
del subsistema, observable en todas las capas:

- la pausa es un NODO NOMBRADO (`await_approval`) → AgentSpan lo registra como
  task por-nodo (no passthrough, L-14) → visible en el trace/explorer;
- la forma pura `run()` devuelve `AwaitingHuman(context)` → el `LocalRuntime` lo
  mapea a `Execution(status="paused", awaiting=context)` (mismo contrato del port);
- la forma `build()` usa `interrupt(context)` de LangGraph → el `AgentSpanRuntime`
  real lo mapea a una HUMAN task de Conductor (mismo contrato `paused`/`resume`).

La lógica (contexto + aplicación) vive UNA sola vez (`_context`/`_apply`) y la
reusan ambas formas → sin drift run↔build (G-DET, L-11).
"""
from __future__ import annotations

from sdk.hitl import human_task
from sdk.runtime import AwaitingHuman


def _context(input: dict) -> dict:
    """El contexto que ve el humano para decidir. Vive UNA vez → run() y el nodo
    `await_approval` (paridad L-11)."""
    return {
        "kind": "budget_change",
        "campaign_id": input["campaign_id"],
        "current_budget_cop": input["current_budget_cop"],
        "proposed_budget_cop": input["proposed_budget_cop"],
        "delta_cop": input["proposed_budget_cop"] - input["current_budget_cop"],
    }


def _apply(input: dict, decision: dict) -> dict:
    """Aplica la decisión humana al cambio propuesto. Vive UNA vez → run() y el
    nodo `apply` (paridad L-11)."""
    if decision["approved"]:
        return {
            "status": "applied",
            "budget_cop": input["proposed_budget_cop"],
            "decided_by": decision["by"],
        }
    return {
        "status": "rejected",
        "budget_cop": input["current_budget_cop"],
        "decided_by": decision["by"],
    }


def run(
    input: dict,
    *,
    ports: dict | None = None,
    tools: dict | None = None,
    decision: dict | None = None,
) -> dict | AwaitingHuman:
    """PURA (G-RUN-SIG + el extra `decision` para HITL). Sin decisión → pide humano
    (`AwaitingHuman`, que el runtime mapea a `paused`); con decisión → aplica."""
    if decision is None:
        return AwaitingHuman(_context(input))
    return _apply(input, decision)


def build(*, checkpointer=None):
    """`StateGraph` con el nodo HITL NOMBRADO `await_approval` (G-SPAN, observable):
    `propose` → `await_approval` (interrupt → pausa durable) → `apply`. Reusa la
    lógica pura de `run()` (G-DET). Pasá un `checkpointer` para recovery/HITL real
    (MemorySaver/SQLite/Postgres). `compile(name=...)` = el nombre que lee AgentSpan."""
    try:
        from typing import TypedDict

        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e

    class State(TypedDict, total=False):
        campaign_id: str
        current_budget_cop: int
        proposed_budget_cop: int
        context: dict   # ← propose (lo que ve el humano)
        decision: dict  # ← await_approval (la respuesta del humano)
        result: dict    # ← apply (output final)

    def propose_node(state: State) -> dict:
        return {"context": _context(state)}

    @human_task(prompt="¿Aprobar el cambio de presupuesto propuesto?")
    def await_approval_node(state: State) -> dict:
        # OFFLINE (LangGraph): corre el body → interrupt() pausa. AGENTSPAN: el body
        # NO corre — se compila como HUMAN task de Conductor; el humano responde vía
        # Result.respond({...}). Mismo nodo, dos runtimes (ver sdk/hitl.py).
        decision = interrupt(state["context"])
        return {"decision": decision}

    def apply_node(state: State) -> dict:
        return {"result": _apply(state, state["decision"])}

    g = StateGraph(State)
    g.add_node("propose", propose_node)
    g.add_node("await_approval", await_approval_node)
    g.add_node("apply", apply_node)
    g.add_edge(START, "propose")
    g.add_edge("propose", "await_approval")
    g.add_edge("await_approval", "apply")
    g.add_edge("apply", END)
    return g.compile(name="budget-approval", checkpointer=checkpointer)
