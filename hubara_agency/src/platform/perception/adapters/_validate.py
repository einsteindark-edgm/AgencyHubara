"""Validación compartida de respuestas tipadas (la forma que exige el contrato)."""
from __future__ import annotations

import math
from typing import Any

from src.platform.perception.ports import TypedAnswer, TypedQuestion


class BadShape(Exception):
    """La respuesta del proveedor no tiene la forma del contrato."""


def _prob(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BadShape("probabilidad no numérica")
    if not 0.0 <= float(value) <= 1.0:
        raise BadShape("probabilidad fuera de [0, 1]")
    return float(value)


def _dist(raw: Any, allowed: tuple[str, ...]) -> tuple[tuple[str, float], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise BadShape("distribución no es un objeto")
    out = tuple((str(k), _prob(v)) for k, v in raw.items())
    if any(k not in allowed for k, _ in out):
        raise BadShape("opción fuera de los criterios")
    total = sum(v for _, v in out)
    if out and not math.isclose(total, 1.0, abs_tol=0.05):
        raise BadShape("la distribución no suma 1")
    return out


def check_answer(q: TypedQuestion, raw: Any) -> TypedAnswer:
    """Respuesta cruda de la Decisions API → `TypedAnswer`, o `BadShape`."""
    if not isinstance(raw, dict) or raw.get("type") != q.kind:
        raise BadShape(f"{q.id}: tipo distinto al de la pregunta")
    if q.kind == "noul":
        return TypedAnswer(id=q.id, kind="noul", p=_prob(raw.get("noul")))
    if q.kind == "choice":
        choice = raw.get("choice")
        if choice not in q.options:
            raise BadShape(f"{q.id}: opción fuera de los criterios")
        probs = _dist(raw.get("probabilities"), q.options) or ((str(choice), 1.0),)
        conf = raw.get("confidence")
        return TypedAnswer(
            id=q.id, kind="choice", choice=str(choice), probs=probs,
            confidence=_prob(conf) if conf is not None else None,
        )
    levels = tuple(str(i) for i in range(len(q.options)))
    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= len(levels) - 1:
        raise BadShape(f"{q.id}: score fuera de la rúbrica")
    conf = raw.get("confidence")
    return TypedAnswer(
        id=q.id, kind="score", score=float(score), probs=_dist(raw.get("probabilities"), levels),
        confidence=_prob(conf) if conf is not None else None,
    )
