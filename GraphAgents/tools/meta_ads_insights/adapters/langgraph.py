"""Adapter LangGraph de `meta-ads-insights`: nodo state->patch. Un nodo es solo un
callable; la impl pura no se toca."""
from __future__ import annotations

from tools.meta_ads_insights.impl import run


def node(state: dict) -> dict:
    return run(payload=state["payload"])
