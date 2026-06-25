"""Golden del nodo HITL en el form de grafo (`build`, langgraph).

El grafo PAUSA en el nodo NOMBRADO `await_approval` (observable: AgentSpan lo
registra como task por-nodo, no passthrough) exponiendo el contexto al humano, y
al reanudar con `Command(resume=decision)` produce el output EXACTO. Paridad con
la forma pura `run()` (L-11): misma lógica → mismo output dada la misma decisión.

Requiere langgraph (interrupt/Command/MemorySaver). El `AgentSpanRuntime` real
mapea este `interrupt()` a una HUMAN task de Conductor — el MISMO contrato
`paused`/`resume(decision)` del runtime port (`test_runtime_hitl.py`).
"""
import pytest

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from graphs.budget_approval import build, run  # noqa: E402
from sdk.runtime import AwaitingHuman  # noqa: E402

_INPUT = {"campaign_id": "c-1", "current_budget_cop": 100_000, "proposed_budget_cop": 250_000}
_CTX = {
    "kind": "budget_change",
    "campaign_id": "c-1",
    "current_budget_cop": 100_000,
    "proposed_budget_cop": 250_000,
    "delta_cop": 150_000,
}


def _cfg(tid: str) -> dict:
    return {"configurable": {"thread_id": tid}}


def test_build_pausa_en_nodo_nombrado_con_contexto() -> None:
    graph = build(checkpointer=MemorySaver())
    cfg = _cfg("appr-1")
    r1 = graph.invoke(_INPUT, cfg)
    # pausó en el nodo HITL NOMBRADO (observable como task por-nodo):
    assert graph.get_state(cfg).next == ("await_approval",)
    # el contexto que ve el humano viajó en el interrupt:
    assert r1["__interrupt__"][0].value == _CTX


def test_build_resume_aprobado_output_exacto() -> None:
    graph = build(checkpointer=MemorySaver())
    cfg = _cfg("appr-2")
    graph.invoke(_INPUT, cfg)
    out = graph.invoke(Command(resume={"approved": True, "by": "ed"}), cfg)
    assert out["result"] == {"status": "applied", "budget_cop": 250_000, "decided_by": "ed"}


def test_build_resume_rechazado_output_exacto() -> None:
    graph = build(checkpointer=MemorySaver())
    cfg = _cfg("appr-3")
    graph.invoke(_INPUT, cfg)
    out = graph.invoke(Command(resume={"approved": False, "by": "ed"}), cfg)
    assert out["result"] == {"status": "rejected", "budget_cop": 100_000, "decided_by": "ed"}


def test_run_paridad_pura_con_build() -> None:
    # forma pura: sin decisión → AwaitingHuman(mismo contexto); con decisión → aplica.
    assert run(_INPUT) == AwaitingHuman(_CTX)
    assert run(_INPUT, decision={"approved": True, "by": "ed"}) == {
        "status": "applied",
        "budget_cop": 250_000,
        "decided_by": "ed",
    }
