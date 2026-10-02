"""Paquete compilado: las tablas de decisión listas para correr.

`load_bundle` certifica y compila; si algo no pasa, lanza `BundleError` con
los diagnósticos (un paquete que no compila no existe). La tabla de una
capacidad decide sobre las respuestas de Jev: la primera fila cuya condición
se cumple decide; `doubt` devuelve `DOUBT` y decide la regla. Una condición
que falla al evaluarse (p. ej. leer una respuesta que no llegó sin
preguntar antes si está) también es duda: el motor nunca tumba al
consumidor.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.platform.decisions.expressions import CompiledExpression, ExpressionError
from src.platform.decisions.model import Capability, ChoiceAnswer

logger = structlog.get_logger()


class _Doubt:
    __slots__ = ()

    def __repr__(self) -> str:
        return "DOUBT"


#: La tabla no decide: decide la regla.
DOUBT: Any = _Doubt()

#: Respuestas normalizadas: noul → p (float); choice → (opción, confianza).
Answers = Mapping[str, "float | tuple[str, float]"]


@dataclass(frozen=True)
class Diagnostic:
    code: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.code} {self.where}: {self.message}"


class BundleError(Exception):
    """El paquete no pasó el certificador."""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics = diagnostics
        super().__init__("; ".join(str(d) for d in diagnostics) or "paquete inválido")


@dataclass(frozen=True)
class CompiledRow:
    when: CompiledExpression | None  # None = otherwise
    then: Any  # el valor, o DOUBT


def default_rule(value: str) -> Any:
    return False if value == "bool" else ""


def normalize_answers(raw: Mapping[str, float | ChoiceAnswer | tuple[str, float]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for qid, answer in raw.items():
        out[qid] = (answer.choice, float(answer.p)) if isinstance(answer, ChoiceAnswer) else answer
    return out


def answers_from_result(questions: Iterable[Any], result: Any) -> dict[str, Any]:
    """Las respuestas de un `PerceptionResult` en la forma de la tabla: la
    probabilidad de cada noul y (opción, su probabilidad) de cada choice.
    Una pregunta sin respuesta no aparece."""
    by_id: dict[Any, Any] = {}
    for answer in getattr(result, "answers", ()) or ():
        by_id.setdefault(getattr(answer, "id", None), answer)  # la primera, como `answer_of`
    out: dict[str, Any] = {}
    for question in questions:
        answer = by_id.get(question.id)
        if answer is None:
            continue
        if question.kind == "noul":
            p = getattr(answer, "p", None)
            if isinstance(p, (int, float)) and not isinstance(p, bool):
                out[question.id] = float(p)
        else:
            choice = getattr(answer, "choice", None)
            if choice:
                probs = dict(getattr(answer, "probs", ()) or ())
                p = probs.get(choice, getattr(answer, "confidence", None))
                conf = float(p) if isinstance(p, (int, float)) and not isinstance(p, bool) else 0.0
                out[question.id] = (str(choice), conf)
    return out


@dataclass(frozen=True)
class CompiledCapability:
    spec: Capability
    rows: tuple[CompiledRow, ...]
    bundle: str = ""

    @property
    def name(self) -> str:
        return self.spec.capability

    @property
    def thresholds(self) -> dict[str, float]:
        return dict(self.spec.thresholds)

    def environment(self, answers: Mapping[str, Any], thresholds: Mapping[str, float] | None, rule: Any) -> dict[str, Any]:
        kinds = {q.id: q.kind for q in self.spec.questions}
        p: dict[str, float] = {}
        choice: dict[str, str] = {}
        conf: dict[str, float] = {}
        for qid, answer in normalize_answers(answers).items():
            if kinds.get(qid) == "noul" and isinstance(answer, (int, float)) and not isinstance(answer, bool):
                p[qid] = float(answer)
            elif kinds.get(qid) == "choice" and isinstance(answer, tuple):
                choice[qid], conf[qid] = str(answer[0]), float(answer[1])
        return {
            "p": p,
            "choice": choice,
            "conf": conf,
            "th": {**self.spec.thresholds, **(thresholds or {})},
            "rule": default_rule(self.spec.value) if rule is None else rule,
        }

    def decide(self, *, answers: Mapping[str, Any], thresholds: Mapping[str, float] | None = None, rule: Any = None) -> Any:
        env = self.environment(answers, thresholds, rule)
        for index, row in enumerate(self.rows):
            if row.when is None:
                return row.then
            try:
                hit = row.when.evaluate(env)
            except ExpressionError as exc:
                logger.warning("decision_bundle.row_failed", capability=self.name, row=index, error=str(exc)[:200])
                return DOUBT
            if hit is True:
                return row.then
        return DOUBT


@dataclass(frozen=True)
class CompiledBundle:
    id: str
    version: int
    oracle: str
    capabilities: Mapping[str, CompiledCapability] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        """`id@versión`: va a la traza de cada decisión."""
        return f"{self.id}@{self.version}"

    def capability(self, name: str) -> CompiledCapability:
        try:
            return self.capabilities[name]
        except KeyError:
            raise KeyError(f"el paquete {self.ref} no tiene la capacidad {name!r}") from None
