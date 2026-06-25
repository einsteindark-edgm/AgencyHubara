"""El CLI `start` — el DISPATCH durable a AgentSpan (lo que el buzón de hubara corre por
SSM: `python -m sdk.cli start <agent> --input ... --runtime agentspan`). Submitea no-bloqueante
(`AgentSpanRuntime.start`) y devuelve el execution-id para pollearlo. Gemelo del `resume`.

Unit: mockea `build_agent` + el runtime (no necesita langgraph/agentspan ni el server).
"""
from __future__ import annotations

import argparse

import sdk.cli as cli


def test_cmd_start_capability_despacha_con_input_crudo(monkeypatch) -> None:
    seen: dict = {}
    monkeypatch.setattr("sdk.loader.build_agent", lambda m, root: "GRAPH")

    def fake_start(self, graph, input):
        seen["graph"] = graph
        seen["input"] = input
        return "exec-7"

    monkeypatch.setattr("sdk.runtime.AgentSpanRuntime.start", fake_start)
    # greeter es una capability (no supervisor) → el input va crudo
    args = argparse.Namespace(id="greeter", input='{"name": "ada"}', runtime="agentspan")
    rc = cli.cmd_start(args)

    assert rc == 0
    assert seen["graph"] == "GRAPH"
    assert seen["input"] == {"name": "ada"}


def test_cmd_start_rechaza_runtime_no_agentspan() -> None:
    args = argparse.Namespace(id="greeter", input="", runtime="local")
    assert cli.cmd_start(args) == 1  # start es SOLO para el dispatch durable
