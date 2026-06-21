"""Adapter LangGraph de `merge-by-date`: nodo state->patch. Un nodo es solo un
callable; la impl pura no se toca."""
from __future__ import annotations

from tools.merge_by_date.impl import run


def node(state: dict) -> dict:
    return run(payload=state["payload"])
