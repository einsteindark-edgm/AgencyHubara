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
from src.platform.decisions.types import ValueType, convert, parse_type, to_cel

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
    then: Any  # el valor ya convertido, o DOUBT (si `expr` es None)
    expr: CompiledExpression | None = None  # `then: {expr: …}`: se calcula


def default_rule(value_type: ValueType | str) -> Any:
    """Lo que vale `rule` cuando quien llama no lo da (los ejemplos)."""
    t = parse_type(value_type) if isinstance(value_type, str) else value_type
    if t.nullable:
        return None
    return {"bool": False, "string": "", "int": 0, "double": 0.0, "list": [], "tuple": [], "record": {}}.get(t.kind)


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
    value_type: ValueType | None = None
    vars: tuple[tuple[str, CompiledExpression], ...] = ()
    constants: Mapping[str, Any] = field(default_factory=dict)
    #: Campos de la entrada que las condiciones leen (`inp.campo`), del catálogo.
    input_fields: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.spec.capability

    @property
    def thresholds(self) -> dict[str, float]:
        return dict(self.spec.thresholds)

    @property
    def type(self) -> ValueType:
        return self.value_type or parse_type(self.spec.value)

    def environment(
        self, answers: Mapping[str, Any], thresholds: Mapping[str, float] | None, rule: Any, inp: Mapping[str, Any] | None
    ) -> dict[str, Any]:
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
            "rule": to_cel(default_rule(self.type) if rule is None else rule),
            "inp": to_cel(dict(inp or {})),
            "consts": to_cel(dict(self.constants)),
            "vars": {},
        }

    def decide(
        self,
        *,
        answers: Mapping[str, Any],
        thresholds: Mapping[str, float] | None = None,
        rule: Any = None,
        inp: Mapping[str, Any] | None = None,
    ) -> Any:
        """El valor de la tabla (en la forma de Python de `value:`), o DOUBT."""
        env = self.environment(answers, thresholds, rule, inp)
        try:
            for name, expression in self.vars:
                env["vars"] = {**env["vars"], name: expression.evaluate(env)}
            for row in self.rows:
                if row.when is not None and row.when.evaluate(env) is not True:
                    continue
                if row.expr is None:
                    return row.then
                return convert(row.expr.evaluate(env), self.type)
        except ExpressionError as exc:
            # Lo normal: leer una respuesta que Jev no dio (la tabla la guarda
            # con `'x' in p`, o la duda es la respuesta correcta).
            logger.debug("decision_bundle.row_unanswered", capability=self.name, error=str(exc)[:200])
            return DOUBT
        except TypeError as exc:  # la salida no es del tipo declarado: un bug del paquete
            logger.warning("decision_bundle.bad_output", capability=self.name, error=str(exc)[:200])
            return DOUBT
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
