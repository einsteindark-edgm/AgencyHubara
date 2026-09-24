"""Arena de los clasificadores (plan §5 punto 8, PR 15). PURO.

Sin etiquetado humano. Desde los resultados de un brazo (uno por caso, lo
que devuelve el sandbox):

  * percepción: turnos medidos, caídas a "turno como hoy" (`fallback`) y
    latencia p50/p95;
  * verificación: decisiones (send / complement / pending) y p95;
  * tasa de complemento y de ronda extra (`turn_policy_extra_round`);
  * costo por turno = LLM + clasificador (percepción + verificación).

`topic_agreement` y `calibration` comparan contra los asuntos que el juez
del scorecard saca POR SU CUENTA (nunca usa la percepción del brazo); los
alimenta la evaluación de la corrida.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from typing import Any

_CLASSIFIER_STEPS = ("perception", "verify")
_BINS = 10


def _quantile(values: list[int], q: float) -> int | None:
    """Rango más cercano (nearest-rank): siempre un valor observado."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _steps(trace: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(trace, dict):
        return []
    return [s for s in trace.get("steps") or [] if isinstance(s, dict)]


def _num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _classifier_cost(result: dict[str, Any]) -> float:
    return sum(
        _num(s.get("cost_usd"))
        for trace in (result.get("trace"), result.get("complement_trace"))
        for s in _steps(trace)
        if s.get("kind") in _CLASSIFIER_STEPS
    )


def _llm_cost(result: dict[str, Any]) -> float:
    llm = result.get("llm_cost_usd")
    return _num(llm if llm is not None else result.get("cost_usd"))


def arena_metrics(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    results = list(results)
    turns = [r for r in results if not r.get("error") and isinstance(r.get("trace"), dict)]
    n = len(turns)
    perception = [s for r in turns for s in _steps(r["trace"]) if s.get("kind") == "perception"]
    verify = [s for r in turns for s in _steps(r["trace"]) if s.get("kind") == "verify"]
    extra = sum(
        1 for r in turns if any(s.get("kind") == "guard" and s.get("name") == "turn_policy_extra_round" for s in _steps(r["trace"]))
    )
    complements = sum(1 for r in turns if isinstance(r.get("complement_trace"), dict))
    classifier = sum(_classifier_cost(r) for r in turns)
    llm = sum(_llm_cost(r) for r in turns)

    def _ms(steps: list[dict[str, Any]]) -> list[int]:
        return [int(s["dur_ms"]) for s in steps if isinstance(s.get("dur_ms"), (int, float))]

    return {
        "turns": n,
        "errors": len(results) - n,
        "perception": {
            "turns": len(perception),
            "fallback_rate": sum(1 for s in perception if s.get("fallback")) / len(perception),
            "p50_ms": _quantile(_ms(perception), 0.50),
            "p95_ms": _quantile(_ms(perception), 0.95),
        }
        if perception
        else None,
        "verify": {
            "turns": len(verify),
            "decisions": dict(Counter(str(s.get("decision") or "send") for s in verify)),
            "p95_ms": _quantile(_ms(verify), 0.95),
        }
        if verify
        else None,
        "complement_rate": complements / n if n else None,
        "extra_round_rate": extra / n if n else None,
        "cost_per_turn_usd": (llm + classifier) / n if n else None,
        "perception_cost_per_turn_usd": classifier / n if n else None,
    }


def topic_agreement(predicted: set[str], judged: set[str]) -> dict[str, float]:
    """Asuntos del brazo contra los del juez. Vacío contra vacío = acuerdo."""
    hit = len(predicted & judged)
    precision = hit / len(predicted) if predicted else 1.0
    recall = hit / len(judged) if judged else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def calibration(pairs: Iterable[tuple[float, bool]]) -> dict[str, Any]:
    """Brier y ECE (10 bins) de (probabilidad dicha, lo que el juez vio)."""
    pairs = [(float(p), bool(y)) for p, y in pairs]
    if not pairs:
        return {"n": 0, "brier": None, "ece": None}
    brier = sum((p - (1.0 if y else 0.0)) ** 2 for p, y in pairs) / len(pairs)
    bins: dict[int, list[tuple[float, bool]]] = {}
    for p, y in pairs:
        bins.setdefault(min(int(p * _BINS), _BINS - 1), []).append((p, y))
    ece = sum(
        abs(sum(p for p, _ in b) / len(b) - sum(1 for _, y in b if y) / len(b)) * len(b) / len(pairs)
        for b in bins.values()
    )
    return {"n": len(pairs), "brier": round(brier, 6), "ece": round(ece, 6)}
