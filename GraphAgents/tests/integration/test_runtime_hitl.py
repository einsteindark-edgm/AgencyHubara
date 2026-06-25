"""HITL en el runtime port (offline, sin langgraph).

Una ejecución que necesita una decisión humana PAUSA durable
(`status="paused"` + `awaiting=<contexto>`), y `resume(eid, decision)` la
completa inyectando la decisión. Se prueba el CONTRATO del port con
`LocalRuntime` (mismo patrón que `test_runtime.py::test_recupera_por_execution_id`),
sin servidor ni langgraph.

El agente puro devuelve un sentinel `AwaitingHuman(context)` cuando pide humano;
el `AgentSpanRuntime` real mapea el `interrupt()` de LangGraph al MISMO contrato
(pausa + resume-con-decisión) — esa unificación es el punto del primitivo.
"""
from __future__ import annotations

from sdk.runtime import AwaitingHuman, LocalRuntime


def _approval_agent(input, *, decision=None):
    """Agente puro mínimo: propone un cambio de presupuesto y PIDE aprobación.

    Sin decisión → `AwaitingHuman(contexto)` (pausa). Con decisión → aplica o
    rechaza. Determinista: mismo input + misma decisión → mismo output.
    """
    delta = input["proposed_budget_cop"] - input["current_budget_cop"]
    if decision is None:
        return AwaitingHuman(
            {
                "kind": "budget_change",
                "campaign_id": input["campaign_id"],
                "current_budget_cop": input["current_budget_cop"],
                "proposed_budget_cop": input["proposed_budget_cop"],
                "delta_cop": delta,
            }
        )
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


_INPUT = {"campaign_id": "c-1", "current_budget_cop": 100_000, "proposed_budget_cop": 250_000}


def test_pausa_esperando_decision_humana() -> None:
    rt = LocalRuntime()
    ex = rt.run(_approval_agent, _INPUT)
    assert ex.status == "paused"
    assert ex.awaiting == {
        "kind": "budget_change",
        "campaign_id": "c-1",
        "current_budget_cop": 100_000,
        "proposed_budget_cop": 250_000,
        "delta_cop": 150_000,
    }
    assert ex.output is None  # todavía no hay output: espera al humano


def test_resume_con_decision_aprobada_completa() -> None:
    rt = LocalRuntime()
    ex = rt.run(_approval_agent, _INPUT)
    out = rt.resume(ex.id, decision={"approved": True, "by": "ed"})
    assert out.id == ex.id
    assert out.status == "completed"
    assert out.output == {"status": "applied", "budget_cop": 250_000, "decided_by": "ed"}


def test_resume_con_decision_rechazada_completa() -> None:
    rt = LocalRuntime()
    ex = rt.run(_approval_agent, _INPUT)
    out = rt.resume(ex.id, decision={"approved": False, "by": "ed"})
    assert out.id == ex.id
    assert out.status == "completed"
    assert out.output == {"status": "rejected", "budget_cop": 100_000, "decided_by": "ed"}


def test_resume_sin_decision_sigue_en_pausa() -> None:
    # resumir SIN decisión no completa: el agente vuelve a pedir humano (re-pausa).
    rt = LocalRuntime()
    ex = rt.run(_approval_agent, _INPUT)
    again = rt.resume(ex.id)
    assert again.status == "paused"
    assert again.output is None
