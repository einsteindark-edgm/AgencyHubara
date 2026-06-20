"""Capability `blended-economics` (analyzer) — el corazón. Esqueleto PURO (G-DET):
compone 3 tools agnósticas del catálogo (inyectadas por el binding): merge-by-date →
blended-unit-economics → diagnose, por día y para el periodo. El periodo agrega los
totales CRUDOS primero y RECIÉN computa (la forma correcta del blend, no un promedio de
ratios). El LLM no aparece acá — eso es el reporter (G-DET: cómputo separado de narrativa).

- run(input, *, ports, tools) — PURA (G-RUN-SIG, golden-replay).
- build()                     — StateGraph LangGraph (G1+).
"""
from __future__ import annotations

_RAW = ("spend_cop", "inline_link_clicks", "conversations_started", "total_orders", "total_revenue_cop")


def _metrics_and_diagnosis(totals: dict, metrics, diag) -> tuple[dict, dict]:
    m = metrics(payload=totals)
    d = diag(payload={"mer": m["mer"], "drop_off_rate": m["drop_off_rate"]})
    return m, d


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    tools = tools or {}
    currency = input.get("currency", "COP")
    if currency != "COP":  # MF-5: COP-only; mezclar monedas en MER/CPA es el bug #1 (cost_unit_lesson)
        raise ValueError(
            f"MVP COP-only; llegó currency={currency!r}. Blendear ventas COP con insights en "
            "otra moneda daría MER/CPA mudos. FX dated es fase 2 — el pod se rehúsa, no adivina."
        )
    merge = tools["merge-by-date"]
    metrics = tools["blended-unit-economics"]
    diag = tools["diagnose"]

    merged = merge(payload={"insights": input["insights"], "sales": input["sales"]})

    days = []
    for d in merged["days"]:
        m, dg = _metrics_and_diagnosis(d, metrics, diag)
        days.append({**d, "metrics": m, "diagnosis": dg})

    period = None
    if merged["days"]:
        agg = {k: sum(d[k] for d in merged["days"]) for k in _RAW}
        m, dg = _metrics_and_diagnosis(agg, metrics, diag)
        period = {**agg, "metrics": m, "diagnosis": dg}

    return {
        "currency": currency,
        "days": days,
        "period": period,
        "unmatched": {"meta_only": merged["meta_only"], "sales_only": merged["sales_only"]},
    }


def build():
    try:
        from langgraph.graph import StateGraph  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e
    raise NotImplementedError("build(): cablear el StateGraph (G1+); el run puro ya está")
