"""Capability `numbers-qa` (analyzer) — el gate NO-self-review. Recomputa las métricas
de cada día con la MISMA tool (blended-unit-economics) y reconcilia byte-for-byte: si
algún número fue editado a mano, no matchea → violación. Además chequea bounds (drop-off
∈ [0,1], win-rate ∈ [0,1], MER ≥ 0). PURO. NO aplica fixes — detecta y decide, sin deuda
silenciosa. Es el "tests verdes ≠ feature viva" sobre data viva (lo que el golden no cubre).

- run(input, *, ports, tools) — PURA (G-RUN-SIG).
- build()                     — StateGraph LangGraph (G1+).
"""
from __future__ import annotations

from decimal import Decimal

_RAW = ("spend_cop", "inline_link_clicks", "conversations_started", "total_orders", "total_revenue_cop")


def _in_unit_interval(v: str) -> bool:
    return Decimal("0") <= Decimal(v) <= Decimal("1")


def _audit(label: str, totals: dict, reported: dict, metrics) -> list:
    """Reconcilia (recomputa byte-for-byte con la MISMA tool) + bounds de las 5 métricas."""
    out: list = []
    if metrics(payload=totals) != reported:
        out.append({"date": label, "issue": "métricas no reconcilian (¿número editado a mano?)"})
    for k in ("drop_off_rate", "global_win_rate"):
        if reported[k] is not None and not _in_unit_interval(reported[k]):
            out.append({"date": label, "issue": f"{k} fuera de [0,1]: {reported[k]}"})
    for k in ("mer", "cost_per_conversation_cop", "global_cpa_cop"):
        if reported[k] is not None and Decimal(reported[k]) < 0:
            out.append({"date": label, "issue": f"{k} negativo: {reported[k]}"})
    return out


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    tools = tools or {}
    metrics = tools["blended-unit-economics"]
    violations: list = []
    for d in input.get("days", []):
        violations += _audit(d["date"], {k: d[k] for k in _RAW}, d["metrics"], metrics)
    period = input.get("period")
    if period:  # MF-2: el verdict de CABECERA sale del periodo → también se reconcilia.
        violations += _audit("TOTAL", {k: period[k] for k in _RAW}, period["metrics"], metrics)
    return {"passed": not violations, "violations": violations}


def build():
    try:
        from langgraph.graph import StateGraph  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e
    raise NotImplementedError("build(): cablear el StateGraph (G1+); el run puro ya está")
