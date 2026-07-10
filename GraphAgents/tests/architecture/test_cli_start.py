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
    monkeypatch.setattr("sdk.loader.build_agent", lambda m, root, ports=None: "GRAPH")

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


def test_cmd_start_inyecta_vendors_reales_para_los_ports_consumidos(monkeypatch) -> None:
    """El buzón despacha `order-sentinel` (capability directa, `consumes: llm`) por SSM:
    cmd_start debe compilar con los VENDORS REALES (`durable_vendors()` — LiteLLMProxy
    para llm, mismo cable que el viewer `_durable_ports`). Sin esto el classify degrada
    a llm_errors en prod con el proxy PERFECTAMENTE configurado."""
    from sdk.connectorkit.ports import LLMPort

    seen: dict = {}

    def fake_build_agent(m, root, ports=None):
        seen["ports"] = ports
        return "GRAPH"

    monkeypatch.setattr("sdk.loader.build_agent", fake_build_agent)
    monkeypatch.setattr("sdk.runtime.AgentSpanRuntime.start", lambda self, g, i: "exec-11")
    _patch_get_sequence(monkeypatch, ["completed"], seen)

    args = argparse.Namespace(id="order-sentinel", input='{"payload": {}}', runtime="agentspan")
    assert cli.cmd_start(args) == 0
    assert seen["ports"] is not None and "llm" in seen["ports"]
    assert isinstance(seen["ports"]["llm"], LLMPort)  # vendor real, no fixture


def test_cmd_start_rechaza_port_consumido_sin_vendor_real(monkeypatch, capsys) -> None:
    """Paridad con el viewer: un agente que consume un port SIN vendor real en el
    durable (ej. meta_marketing_api — G2) se rechaza LOUD antes de submitear,
    no falla críptico adentro de Conductor."""
    monkeypatch.setattr(
        "sdk.loader.build_agent",
        lambda m, root, ports=None: (_ for _ in ()).throw(AssertionError("no debe compilar")),
    )
    args = argparse.Namespace(id="meta-insights", input="{}", runtime="agentspan")
    assert cli.cmd_start(args) == 1
    assert "meta_marketing_api" in capsys.readouterr().out


def test_cmd_start_rechaza_runtime_no_agentspan() -> None:
    args = argparse.Namespace(id="greeter", input="", runtime="local")
    assert cli.cmd_start(args) == 1  # start es SOLO para el dispatch durable


class _FakeExec:
    def __init__(self, status): self.status = status


def _patch_get_sequence(monkeypatch, statuses, seen):
    def fake_get(self, eid):
        seen.setdefault("gets", []).append(eid)
        return _FakeExec(statuses[min(len(seen["gets"]) - 1, len(statuses) - 1)])
    monkeypatch.setattr("sdk.runtime.AgentSpanRuntime.get", fake_get)
    monkeypatch.setattr("sdk.cli._WAIT_POLL_SECONDS", 0)


def test_cmd_start_espera_el_terminal_antes_de_salir(monkeypatch, capsys) -> None:
    # Eslabón 5 (2026-07-10, runs 5053a533/13622474 RUNNING eternos): los task
    # workers de AgentSpan viven DENTRO del proceso del CLI (thread daemon del
    # runtime.start). Si el CLI imprime el eid y SALE, los workers mueren y las
    # tasks quedan huérfanas — todo dispatch por SSM stalleaba. El CLI debe
    # quedarse vivo hasta un estado terminal (completed/failed/paused-HITL).
    seen: dict = {}
    monkeypatch.setattr("sdk.loader.build_agent", lambda m, root, ports=None: "GRAPH")
    monkeypatch.setattr(
        "sdk.runtime.AgentSpanRuntime.start", lambda self, g, i: "exec-9"
    )
    _patch_get_sequence(monkeypatch, ["running", "running", "completed"], seen)

    args = argparse.Namespace(id="greeter", input="{}", runtime="agentspan")
    rc = cli.cmd_start(args)

    assert rc == 0
    assert len(seen["gets"]) >= 3, "debe pollear hasta el terminal"
    out = capsys.readouterr().out
    assert out.startswith("execution exec-9: started"), (
        "el eid va PRIMERO en stdout (el buzón lo parsea aunque el wait toque techo)"
    )


def test_cmd_start_paused_hitl_tambien_es_terminal(monkeypatch) -> None:
    # awaiting_approval (HUMAN task): nada más que ejecutar localmente hasta el
    # resume — el CLI no debe colgarse esperando al humano.
    seen: dict = {}
    monkeypatch.setattr("sdk.loader.build_agent", lambda m, root, ports=None: "GRAPH")
    monkeypatch.setattr(
        "sdk.runtime.AgentSpanRuntime.start", lambda self, g, i: "exec-10"
    )
    _patch_get_sequence(monkeypatch, ["running", "paused"], seen)

    args = argparse.Namespace(id="greeter", input="{}", runtime="agentspan")
    assert cli.cmd_start(args) == 0
    assert len(seen["gets"]) == 2
