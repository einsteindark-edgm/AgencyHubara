"""El loader threadea la `decision` del HITL por `build_runnable`.

Un capability HITL (`run()` devuelve `AwaitingHuman` sin decisión) corrido por el
runtime port a través de `build_runnable`: `LocalRuntime.run` lo PAUSA, y
`resume(eid, decision)` debe llegar hasta `run(input, decision=...)`. Sin el
threading, el `runnable(input)` del loader no acepta `decision` → TypeError.

PURO/offline (no usa langgraph: la forma `run()` de budget_approval no lo importa).
"""
from __future__ import annotations

from pathlib import Path

from sdk.loader import build_runnable
from sdk.manifest_model import AgentNode
from sdk.runtime import LocalRuntime

ROOT = Path(__file__).resolve().parents[2]  # .../GraphAgents

_INPUT = {"campaign_id": "c-1", "current_budget_cop": 100_000, "proposed_budget_cop": 250_000}


def test_build_runnable_threadea_la_decision_del_hitl() -> None:
    node = AgentNode(name="budget-approval", archetype="analyzer", capability="graphs.budget_approval:build")
    runnable = build_runnable(node, ROOT)
    rt = LocalRuntime()

    ex = rt.run(runnable, _INPUT)
    assert ex.status == "paused"  # run() devolvió AwaitingHuman → LocalRuntime pausó

    out = rt.resume(ex.id, decision={"approved": True, "by": "ed"})
    assert out.status == "completed"
    assert out.output == {"status": "applied", "budget_cop": 250_000, "decided_by": "ed"}
