"""El CLI `resume` — completa la HUMAN task de un HITL.

Resume un execution-id con la decisión vía `AgentSpanRuntime` (lo que el buzón de
hubara corre por SSM en la caja: `python -m sdk.cli resume <eid> --decision ...`).
Unit: mockea el runtime (no necesita el server `:6767`).
"""
from __future__ import annotations

import argparse

import sdk.cli as cli
from sdk.runtime import Execution


def test_cmd_resume_pasa_execution_id_y_decision_parseada(monkeypatch) -> None:
    seen: dict = {}

    def fake_resume(self, execution_id, decision=None):
        seen["eid"] = execution_id
        seen["decision"] = decision
        return Execution(id=execution_id, status="completed", output={"status": "applied"})

    monkeypatch.setattr("sdk.runtime.AgentSpanRuntime.resume", fake_resume)
    args = argparse.Namespace(execution_id="exec-7", decision='{"approved": true, "by": "ed"}')
    rc = cli.cmd_resume(args)

    assert rc == 0
    assert seen["eid"] == "exec-7"
    assert seen["decision"] == {"approved": True, "by": "ed"}


def test_cmd_resume_sin_decision_no_inyecta_nada(monkeypatch) -> None:
    seen: dict = {}

    def fake_resume(self, execution_id, decision=None):
        seen["decision"] = decision
        return Execution(id=execution_id, status="paused")

    monkeypatch.setattr("sdk.runtime.AgentSpanRuntime.resume", fake_resume)
    args = argparse.Namespace(execution_id="e", decision="")
    cli.cmd_resume(args)
    assert seen["decision"] is None
