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


#: Totales del scorecard que se SUMAN hacia arriba (anuncios → segmento → campaña). El
#: alcance no (personas repetidas entre anuncios).
_SCORE_ADDITIVE = (
    "spend_cop", "impressions", "link_clicks", "conversations", "chats",
    "paid_sales", "paid_revenue_cop", "pending_sales", "pending_revenue_cop",
    "unverified_sales", "unverified_revenue_cop", "cancelled_sales", "cancelled_revenue_cop",
    "open_chats", "stale_open_chats",
)
_ECON_INPUT = ("spend_cop", "impressions", "reach", "link_clicks", "conversations",
               "chats", "paid_sales", "paid_revenue_cop")


def _audit_entity(label: str, entity: dict, economics) -> list:
    """Recomputa las métricas de UNA entidad del scorecard con la MISMA tool y reconcilia."""
    t = entity["totals"]
    if economics(payload={k: t.get(k) for k in _ECON_INPUT}) != entity["metrics"]:
        return [{"date": label, "issue": "métricas no reconcilian (¿número editado a mano?)"}]
    return []


def _audit_sum(label: str, parent: dict, children: list[dict]) -> list:
    bad = [k for k in _SCORE_ADDITIVE if parent[k] != sum(c[k] for c in children)]
    return [{"date": label, "issue": f"los totales de abajo no suman: {', '.join(bad)}"}] if bad else []


def _audit_scorecard(sc: dict, economics) -> list:
    """El drill-down: cada entidad reconcilia y la jerarquía cuadra."""
    out: list = _audit_entity("campaña", sc["campaign"], economics)
    for s in sc["segments"]:
        out += _audit_entity(f"segmento {s['label']}", s, economics)
        members = [a["totals"] for a in sc["ads"] if a["segment_id"] == s["id"]]
        out += _audit_sum(f"segmento {s['label']}", s["totals"], members)
    for a in sc["ads"]:
        out += _audit_entity(f"anuncio {a['label']} ({a['segment_label']})", a, economics)
    out += _audit_sum("campaña", sc["campaign"]["totals"], [s["totals"] for s in sc["segments"]])
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
    scorecard = input.get("scorecard")
    if scorecard:  # el drill-down por campaña (2026-09-25) también se reconcilia
        violations += _audit_scorecard(scorecard, tools["entity-economics"])
    return {"passed": not violations, "violations": violations}


def build():
    """`StateGraph` LangGraph (G1) — single-node que REUSA el `run()` puro (el gate
    no-self-review recomputa con la MISMA tool y reconcilia; la lógica vive UNA vez, G-DET).
    AgentSpan lo corre por passthrough. Durabilidad: el grafo entero como UNA task (L-11)."""
    try:
        from typing import TypedDict

        from langgraph.graph import END, START, StateGraph
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e

    from tools.blended_unit_economics.impl import run as metrics
    from tools.entity_economics.impl import run as entity_economics

    class State(TypedDict, total=False):
        days: list          # blended-economics
        period: dict        # blended-economics (None si no hay días en común)
        scorecard: dict     # ctwa-scorecard (None en análisis de cuenta completa)
        passed: bool        # ← run()
        violations: list    # ← run()

    def qa(state: State) -> dict:
        return run(dict(state), tools={"blended-unit-economics": metrics,
                                       "entity-economics": entity_economics})

    g = StateGraph(State)
    g.add_node("qa", qa)
    g.add_edge(START, "qa")
    g.add_edge("qa", END)
    return g.compile(name="numbers-qa")
