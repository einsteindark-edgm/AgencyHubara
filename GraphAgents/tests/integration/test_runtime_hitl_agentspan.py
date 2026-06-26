"""Round-trip HITL contra el runtime REAL (AgentSpan/Conductor en :6767).

Codifica lo que el probe confirmó en vivo: el grafo con `@human_task` PAUSA como
HUMAN task de Conductor, y `AgentSpanRuntime.resume(eid, decision)` la completa
(vía `respond({"decision": decision})`) → corre `apply` y termina con el output
aplicado.

Skip sin server / sin agentspan. Crea ejecuciones reales en el Conductor de dev.
Correr con el venv que tiene agentspan: `.venv/bin/python -m pytest`.
"""
import time

import pytest

pytest.importorskip("agentspan")
pytest.importorskip("langgraph")

from sdk.runtime import AgentSpanRuntime  # noqa: E402
from sdk.trace import fetch_workflow  # noqa: E402


def _server_up() -> bool:
    try:
        import urllib.request

        urllib.request.urlopen("http://localhost:6767/api/workflow/search?size=1", timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _server_up(), reason="no hay server AgentSpan en :6767")

_INPUT = {"campaign_id": "c-1", "current_budget_cop": 100_000, "proposed_budget_cop": 250_000}


def _wait(eid: str, pred, tries: int = 20) -> dict | None:
    for _ in range(tries):
        time.sleep(1.0)
        wf = fetch_workflow(eid)
        if pred(wf):
            return wf
    return None


def _has_human_waiting(wf: dict) -> bool:
    return any(
        t.get("taskType") == "HUMAN" and t.get("status") == "IN_PROGRESS"
        for t in wf.get("tasks", [])
    )


def test_human_task_pausa_y_resume_completa_el_round_trip() -> None:
    from graphs.budget_approval import build

    rt = AgentSpanRuntime()
    eid = rt.start(build(), _INPUT)

    # 1. el grafo PAUSA como HUMAN task de Conductor
    assert _wait(eid, _has_human_waiting) is not None, "debía pausar como HUMAN task"

    # 2. resume con la decisión → completa la HUMAN task (respond) → corre apply
    rt.resume(eid, decision={"approved": True, "by": "ed"})

    done = _wait(eid, lambda wf: wf.get("status") not in ("RUNNING", None))
    assert done is not None and done.get("status") == "COMPLETED"

    # 3. la decisión fluyó al apply → output aplicado (AgentSpan envuelve como
    #    {'result': '<repr del state>'}, L-8; chequeamos el contenido).
    out = str(done.get("output") or "")
    assert "applied" in out and "250000" in out and "ed" in out
