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
alimenta la evaluación de la corrida. El juez (EST-08) escribe cada asunto en
texto libre: `judge_topic_codes` lo lleva a los 17 códigos del clasificador
por palabras clave (al comienzo de una palabra: "apagó" no es "pago"). Es un
mapeo APROXIMADO (lo que no calza se cuenta aparte): sirve para comparar B
contra C con la misma vara, no como verdad absoluta. Sin predicciones no hay
precisión (None, no 1,0): un clasificador que siempre cae a "turno como hoy"
no puede verse perfecto.
"""
from __future__ import annotations

import math
import re
import unicodedata
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


# Del más específico al más general: el primer código que calza se queda con
# el asunto ("datos de envío" es datos_envio, no envio; "costo del envío" es
# envio, no precio; "enviar fotos" es foto, no envio).
TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("estado_pedido", ("estado del pedido", "mi pedido", "seguimiento", "guia", "ya pague")),
    ("datos_envio", ("datos de envio", "direccion", "quien recibe", "barrio", "datos para el envio")),
    ("tiempos", ("tiempo", "tarda", "demora", "cuando llega", "fecha de entrega", "cuando esta listo")),
    ("foto", ("foto", "imagen")),
    ("envio", ("envio", "domicilio", "despacho", "flete", "enviar")),
    ("pagos", ("pago", "pagar", "nequi", "transferencia", "daviplata", "tarjeta", "efectivo", "contraentrega")),
    ("catalogo", ("catalogo", "disenos", "modelos", "opciones", "productos")),
    ("precio", ("precio", "cuanto", "vale", "cuesta", "costo", "valor")),
    ("medidas", ("medida", "tamano", "alto", "ancho", "cm")),
    ("variante", ("color", "variante")),
    ("aroma", ("aroma", "olor", "fragancia")),
    ("personalizacion", ("personaliz", "dedicatoria", "grabad", "con su nombre", "con el nombre")),
    ("disponibilidad", ("disponib", "stock", "agotad")),
    ("confirma_compra", ("confirma", "quiero comprar", "lo quiero", "compra")),
    ("aplaza", ("luego", "despues", "otro dia", "aplaz", "mas tarde")),
    ("queja", ("queja", "problema", "reclamo", "llego mal", "roto")),
    ("saludo", ("saludo", "hola", "buenas")),
)
# Cada clave calza al comienzo de una palabra (o pegada a un número: "20cm").
_TOPIC_PATTERNS = tuple(
    (code, re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(w) for w in words) + ")")) for code, words in TOPIC_KEYWORDS
)


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(c))


def judge_topic_codes(topics: Iterable[dict[str, Any]]) -> tuple[set[str], int]:
    """(códigos de los asuntos del juez, cuántos no calzaron con ninguno)."""
    codes: set[str] = set()
    unmapped = 0
    for topic in topics:
        text = _plain(str(topic.get("topic") or "")) if isinstance(topic, dict) else ""
        code = next((c for c, pattern in _TOPIC_PATTERNS if pattern.search(text)), None)
        if code is None:
            unmapped += 1
        else:
            codes.add(code)
    return codes, unmapped


def perceived_topics(trace: dict[str, Any] | None) -> dict[str, tuple[float, bool]] | None:
    """Lo que el clasificador dijo de cada asunto en el turno: (p, lo marcó).
    None si el turno no tuvo percepción."""
    step = next((s for s in _steps(trace) if s.get("kind") == "perception"), None)
    if step is None:
        return None
    out: dict[str, tuple[float, bool]] = {}
    for a in step.get("answers") or []:
        q = a.get("q") if isinstance(a, dict) else None
        if isinstance(q, str) and q.startswith("topic.") and isinstance(a.get("p"), (int, float)):
            out[q.removeprefix("topic.")] = (float(a["p"]), a.get("picked") is True)
    return out


def topic_arena(pairs: Iterable[tuple[dict[str, tuple[float, bool]], set[str]]]) -> dict[str, Any]:
    """Acuerdo (micro, sobre todos los turnos) y calibración contra el juez."""
    pairs = list(pairs)
    if not pairs:
        return {"turns": 0, "precision": None, "recall": None, "f1": None, "calibration": calibration([])}
    hits = predicted = judged_total = 0
    probs: list[tuple[float, bool]] = []
    for perceived, judged in pairs:
        picked = {code for code, (_, mark) in perceived.items() if mark}
        hits += len(picked & judged)
        predicted += len(picked)
        judged_total += len(judged)
        probs.extend((p, code in judged) for code, (p, _) in perceived.items())
    precision = hits / predicted if predicted else None
    recall = hits / judged_total if judged_total else None
    # F1 micro = 2·aciertos / (predichos + del juez): definido aunque un lado esté vacío.
    f1 = 2 * hits / (predicted + judged_total) if predicted + judged_total else None
    return {"turns": len(pairs), "precision": precision, "recall": recall, "f1": f1, "calibration": calibration(probs)}
