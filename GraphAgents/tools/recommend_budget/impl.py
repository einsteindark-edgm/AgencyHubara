"""Lógica PURA de `recommend-budget` — G-AGNOSTIC: NO importa ningún runtime
(langgraph/agentspan/langchain). Reparte `total_budget` entre adsets proporcional
a su ROAS. Determinista e idempotente: misma entrada → misma salida.
"""
from __future__ import annotations


def run(*, adsets: list[dict], total_budget: float) -> dict:
    """[{id, roas}] + total → {allocations: [{id, budget}]}.

    Si ningún adset tiene ROAS positivo, reparte uniforme.
    """
    weights = [max(float(a.get("roas", 0.0)), 0.0) for a in adsets]
    total_w = sum(weights)
    if total_w <= 0:
        share = round(total_budget / len(adsets), 2) if adsets else 0.0
        allocations = [{"id": a["id"], "budget": share} for a in adsets]
    else:
        allocations = [
            {"id": a["id"], "budget": round(total_budget * w / total_w, 2)}
            for a, w in zip(adsets, weights)
        ]
    return {"allocations": allocations}
