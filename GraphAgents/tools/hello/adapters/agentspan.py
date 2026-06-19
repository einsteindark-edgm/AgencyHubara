"""Adapter AgentSpan de `hello` — `@tool` (import del runtime dentro de la función)."""
from __future__ import annotations

from tools.hello.impl import run


def as_agentspan_tool():
    from agentspan.agents import tool  # type: ignore

    @tool
    def hello(name: str) -> dict:
        """Saluda por nombre."""
        return run(name=name)

    return hello
