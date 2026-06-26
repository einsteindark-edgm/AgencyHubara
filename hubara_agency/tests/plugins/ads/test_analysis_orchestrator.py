"""Orchestrator del buzón — `apply_state` diffea el estado interpretado de Conductor
contra el record y, si cambió, appendea el evento del bridge (idempotente por status) y lo
devuelve para que el caller lo publique al bus SSE. Es lo que convierte el POLL en eventos.
"""
from src.plugins.ads.runs import orchestrator, record


def test_apply_state_emite_run_started_al_pasar_a_running() -> None:
    record.create_run("r1", agent="a", input={})
    ev = orchestrator.apply_state("r1", {"status": "running", "awaiting": None, "result": None, "error": None})
    assert ev["type"] == "run.started"
    assert record.read_run("r1")["status"] == "running"


def test_apply_state_sin_cambio_no_reemite() -> None:
    record.create_run("r2", agent="a", input={})
    s = {"status": "running", "awaiting": None, "result": None, "error": None}
    orchestrator.apply_state("r2", s)
    assert orchestrator.apply_state("r2", s) is None  # mismo status → idempotente


def test_apply_state_awaiting_lleva_el_contexto() -> None:
    record.create_run("r3", agent="a", input={})
    ev = orchestrator.apply_state(
        "r3", {"status": "awaiting_approval", "awaiting": {"q": "?"}, "result": None, "error": None}
    )
    assert ev["type"] == "run.awaiting_approval"
    assert ev["payload"]["context"] == {"q": "?"}
    assert record.read_run("r3")["awaiting"] == {"q": "?"}


def test_apply_state_completed_lleva_el_output() -> None:
    record.create_run("r4", agent="a", input={})
    ev = orchestrator.apply_state(
        "r4", {"status": "completed", "awaiting": None, "result": {"n": 5}, "error": None}
    )
    assert ev["type"] == "run.result"
    assert ev["payload"]["output"] == {"n": 5}
    assert record.read_run("r4")["result"] == {"n": 5}
