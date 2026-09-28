"""Adapter LangGraph de `entity-economics`: nodo state->patch. Un nodo es solo un
callable; la impl pura no se toca."""
from __future__ import annotations

from tools.entity_economics.impl import run


def node(state: dict) -> dict:
    return run(payload=state["payload"])
