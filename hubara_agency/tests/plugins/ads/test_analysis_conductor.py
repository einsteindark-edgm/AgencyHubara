"""Interpret de Conductor — el corazón del relay (poll-based).

`interpret(workflow)` toma el JSON de una ejecución de Conductor (`:6767/api`) y deriva
el estado lógico del run para el record/SSE: running · awaiting_approval (HUMAN task
IN_PROGRESS, HITL) · completed (+ output desenvuelto) · failed. PURO (fixture sintético,
sin red), mismo borde HTTP que GraphAgents pero re-implementado del lado hubara (los
subsistemas no comparten código — solo el contrato REST de Conductor).
"""
from src.plugins.ads.runs import conductor


def test_running_sin_human_task() -> None:
    wf = {"status": "RUNNING", "tasks": [{"referenceTaskName": "x_propose", "status": "COMPLETED"}]}
    assert conductor.interpret(wf) == {"status": "running", "awaiting": None, "result": None, "error": None}


def test_human_task_in_progress_es_awaiting() -> None:
    wf = {
        "status": "RUNNING",
        "tasks": [
            {"referenceTaskName": "x_propose", "status": "COMPLETED"},
            {"referenceTaskName": "human_await_approval", "taskType": "HUMAN", "status": "IN_PROGRESS",
             "inputData": {"context": {"kind": "budget_change", "delta_cop": 150000}}},
        ],
    }
    s = conductor.interpret(wf)
    assert s["status"] == "awaiting_approval"
    assert s["awaiting"] == {"kind": "budget_change", "delta_cop": 150000}


def test_completed_desenvuelve_el_output() -> None:
    # AgentSpan envuelve el output como {'result': '<repr Python del state>'} (L-8).
    wf = {"status": "COMPLETED", "output": {"result": "{'status': 'applied', 'n': 5}"}, "tasks": []}
    s = conductor.interpret(wf)
    assert s["status"] == "completed"
    assert s["result"] == {"status": "applied", "n": 5}


def test_failed_lleva_la_razon() -> None:
    wf = {"status": "FAILED", "reasonForIncompletion": "node boom", "tasks": []}
    s = conductor.interpret(wf)
    assert s["status"] == "failed"
    assert s["error"] == "node boom"
