"""Adapter LangGraph de `hello` — un nodo `state -> patch`."""
from __future__ import annotations

from tools.hello.impl import run


def node(state: dict) -> dict:
    return run(name=state["name"])
