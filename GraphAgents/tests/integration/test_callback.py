"""El emisor de callbacks del puente (GraphAgents → backend), capa PURA.

`to_event(execution, *, run_id, seq)` mapea una `Execution` del runtime a un EVENTO
del protocolo del puente. PURO (G-DET): sin red, sin time/random. La idempotencia
del consumidor es por `event_id` DETERMINISTA (`<run_id>:<seq>`). El envío real
(HTTP + retry) es un sink/vendor aparte (G-PORT) — esta capa no toca la red.

El vocabulario de eventos es el contrato que consume el buzón en hubara:
run.started · run.awaiting_approval · run.result · run.failed.
"""
from sdk.callback import to_event
from sdk.runtime import Execution


def test_paused_se_mapea_a_awaiting_approval() -> None:
    ex = Execution(
        id="local-000001", status="paused",
        awaiting={"kind": "budget_change", "delta_cop": 150_000},
    )
    ev = to_event(ex, run_id="run-7", seq=2)
    assert ev == {
        "run_id": "run-7",
        "execution_id": "local-000001",
        "event_id": "run-7:2",
        "type": "run.awaiting_approval",
        "payload": {"context": {"kind": "budget_change", "delta_cop": 150_000}},
    }


def test_completed_se_mapea_a_result() -> None:
    ex = Execution(id="e1", status="completed", output={"status": "applied", "budget_cop": 250_000})
    ev = to_event(ex, run_id="r", seq=5)
    assert ev == {
        "run_id": "r", "execution_id": "e1", "event_id": "r:5",
        "type": "run.result", "payload": {"output": {"status": "applied", "budget_cop": 250_000}},
    }


def test_failed_se_mapea_a_failed() -> None:
    ex = Execution(id="e2", status="failed", error="boom")
    ev = to_event(ex, run_id="r", seq=1)
    assert ev == {
        "run_id": "r", "execution_id": "e2", "event_id": "r:1",
        "type": "run.failed", "payload": {"error": "boom"},
    }


def test_running_se_mapea_a_started() -> None:
    ex = Execution(id="e3", status="running")
    ev = to_event(ex, run_id="r", seq=0)
    assert ev == {
        "run_id": "r", "execution_id": "e3", "event_id": "r:0",
        "type": "run.started", "payload": {},
    }


def test_event_id_determinista_por_run_y_seq() -> None:
    # idempotencia: mismo (run_id, seq) → mismo event_id, SIN time/random (G-DET/L-1).
    a = to_event(Execution(id="x", status="running"), run_id="run-9", seq=3)
    b = to_event(Execution(id="x", status="running"), run_id="run-9", seq=3)
    assert a["event_id"] == b["event_id"] == "run-9:3"
