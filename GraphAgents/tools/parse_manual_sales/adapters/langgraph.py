"""Adapter LangGraph de `parse-manual-sales`: nodo state->patch. Un nodo es solo un
callable; la impl pura no se toca."""
from __future__ import annotations

from tools.parse_manual_sales.impl import run


def node(state: dict) -> dict:
    return run(payload=state["payload"])
