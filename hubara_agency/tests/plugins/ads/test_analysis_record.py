"""Run-record del buzón de análisis (plugin ads) — el espejo FINO de un run, en el vault.

`create_run` nace pending; `append_event` es idempotente por `event_id`, deriva el
status del tipo de evento, y captura `awaiting` (el contexto HITL) y `result`. Persiste
en el vault (aislado por el fixture autouse `_isolate_vault_dir`). Es la vista que el SSE
relaya y que sobrevive un refresh del browser / restart del backend.
"""
from src.plugins.ads.runs import record


def test_create_run_nace_pending_y_persiste() -> None:
    r = record.create_run("run-1", agent="ads-analytics", input={"x": 1})
    assert r["status"] == "pending"
    assert r["events"] == []
    assert record.read_run("run-1")["status"] == "pending"  # sobrevive una re-lectura


def test_append_event_deriva_status_captura_awaiting_y_es_idempotente() -> None:
    record.create_run("run-2", agent="a", input={})
    record.append_event("run-2", {"event_id": "run-2:1", "type": "run.started", "payload": {}})
    r = record.append_event(
        "run-2",
        {"event_id": "run-2:2", "type": "run.awaiting_approval", "payload": {"context": {"q": "¿aprobar?"}}},
    )
    assert r["status"] == "awaiting_approval"
    assert r["awaiting"] == {"q": "¿aprobar?"}
    assert len(r["events"]) == 2
    # idempotencia: el mismo event_id NO duplica
    again = record.append_event("run-2", {"event_id": "run-2:2", "type": "run.awaiting_approval", "payload": {}})
    assert len(again["events"]) == 2


def test_run_result_completa_guarda_output_y_persiste() -> None:
    record.create_run("run-3", agent="a", input={})
    r = record.append_event(
        "run-3",
        {"event_id": "run-3:9", "type": "run.result", "payload": {"output": {"status": "applied", "n": 5}}},
    )
    assert r["status"] == "completed"
    assert r["result"] == {"status": "applied", "n": 5}
    assert record.read_run("run-3")["result"] == {"status": "applied", "n": 5}  # persiste


def test_append_event_a_run_inexistente_falla_loud() -> None:
    import pytest

    with pytest.raises(KeyError):
        record.append_event("no-existe", {"event_id": "x", "type": "run.started"})


def test_run_failed_guarda_el_error_en_el_record() -> None:
    """`run.failed` hoistea el `error` del payload al record (la UI lee `record.error`)."""
    record.create_run("rf", agent="ads-analytics", input={})
    r = record.append_event(
        "rf", {"event_id": "rf:1", "type": "run.failed", "payload": {"error": "boom en el nodo X"}}
    )
    assert r["status"] == "failed"
    assert r["error"] == "boom en el nodo X"
    assert record.read_run("rf")["error"] == "boom en el nodo X"  # persiste
