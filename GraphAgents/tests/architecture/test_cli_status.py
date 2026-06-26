"""El CLI `status` — el POLL del progreso de un run durable.

Imprime a stdout el workflow JSON CRUDO de Conductor (con `tasks[]`) de un execution-id; lo corre
el buzón de hubara por SSM en la caja (`python -m sdk.cli status <eid> --runtime agentspan`) y lo
`interpret`a del lado hubara — el buzón NUNCA se conecta directo a Conductor, solo lee este stdout.
Unit: mockea `trace.fetch_workflow` (no necesita el server `:6767`).
"""
from __future__ import annotations

import argparse
import json

import sdk.cli as cli


def test_cmd_status_imprime_el_workflow_json_crudo_de_conductor(monkeypatch, capsys) -> None:
    wf = {
        "workflowId": "w1",
        "status": "RUNNING",
        "tasks": [{"taskType": "HUMAN", "status": "IN_PROGRESS", "inputData": {"context": {"q": "?"}}}],
    }
    seen: dict = {}

    def fake_fetch(execution_id, server_url=None, timeout=8):
        seen["eid"] = execution_id
        seen["server_url"] = server_url  # None → la caja consulta su Conductor LOCAL (localhost:6767)
        return wf

    monkeypatch.setattr("sdk.trace.fetch_workflow", fake_fetch)
    args = argparse.Namespace(execution_id="exec-9", runtime="agentspan")
    rc = cli.cmd_status(args)

    assert rc == 0
    assert seen["eid"] == "exec-9"
    assert seen["server_url"] is None  # NO se le pasa una URL remota: la caja usa su propio Conductor
    out = json.loads(capsys.readouterr().out)  # stdout = el workflow JSON crudo, parseable por el buzón
    assert out == wf


def test_cmd_status_exige_runtime_agentspan(monkeypatch) -> None:
    # status es SOLO para el poll durable (Conductor); sin --runtime agentspan, no-op rc=1, no toca Conductor.
    called = {"n": 0}
    monkeypatch.setattr("sdk.trace.fetch_workflow", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    args = argparse.Namespace(execution_id="e", runtime="local")
    assert cli.cmd_status(args) == 1
    assert called["n"] == 0
