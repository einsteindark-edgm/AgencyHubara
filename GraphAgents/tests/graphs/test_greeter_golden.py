"""G1 · el `build()` de greeter es un `StateGraph` LangGraph REAL (no el stub).

Requiere langgraph → corre en el container (`/opt/venv/bin/python -m pytest`) o en
CI; el python del sistema (sin langgraph) lo **SKIPea** (`importorskip`), así el
loop local sigue verde. El `run()` puro ya tiene su golden (test_hello / runtime);
acá se asierta el GRAFO COMPILADO: mismo output que el puro + el nombre que lee
AgentSpan de `compile(name=...)`.
"""
from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

from graphs.greeter import build


def test_greeter_stategraph_golden():
    graph = build()
    out = graph.invoke({"name": "mundo"})
    assert out["greeting"] == "hola, mundo"


def test_greeter_stategraph_carries_name():
    # el nombre vive en el grafo compilado — AgentSpan lo lee de `compile(name=...)`.
    graph = build()
    assert getattr(graph, "name", None) == "greeter"
