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

import re

import structlog

from src.platform.decisions.expressions import CompiledExpression, ExpressionError
from src.platform.decisions.model import Capability, ChoiceAnswer, Question
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


#: Un `{campo}` de las plantillas de `each` (id y texto).
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
#: Lo que cada ítem trae además de sus campos.
ITEM_POSITION = ("n", "index")
#: Lo que la tabla lee de la respuesta de cada ítem.
ITEM_ANSWER = ("p", "choice", "conf")


@dataclass(frozen=True)
class ItemQuestion:
    """La pregunta de un ítem, ya con su id y su texto."""

    id: str
    kind: str
    text: str
    criteria: Mapping[str, str]


def render(template: str, item: Mapping[str, Any]) -> str:
    """La plantilla con cada `{campo}` reemplazado por el del ítem."""
    return PLACEHOLDER.sub(lambda m: str(item.get(m.group(1), m.group(0))), template)


@dataclass(frozen=True)
class CompiledRow:
    when: CompiledExpression | None  # None = otherwise
    then: Any  # el valor ya convertido, o DOUBT (si `expr` es None)
    expr: CompiledExpression | None = None  # `then: {expr: …}`: se calcula


def default_rule(value_type: ValueType | str) -> Any:
    """Lo que vale `rule` cuando quien llama no lo da (los ejemplos)."""
    t = parse_type(value_type) if isinstance(value_type, str) else value_type
    if t.kind == "union":
        return default_rule(t.items[0])
    if t.nullable:
        return None
    if t.kind == "fixed":
        return [default_rule(item) for item in t.items]
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
    #: Campos de la entrada que las condiciones leen (`inp.campo`): los del
    #: catálogo y, después, los que deriva la vista.
    input_fields: tuple[str, ...] = ()
    #: Condición compilada de cada pregunta condicional (id → `when`).
    conditions: tuple[tuple[str, CompiledExpression], ...] = ()
    #: `each.when` compilado (qué ítems se preguntan).
    each_when: CompiledExpression | None = None
    #: Variantes del texto de `each`: (condición o None = otherwise, plantilla).
    each_texts: tuple[tuple[CompiledExpression | None, str], ...] = ()

    @property
    def name(self) -> str:
        return self.spec.capability

    @property
    def control(self) -> str:
        """El interruptor que comparte (una variante) o el suyo."""
        return self.spec.control or self.spec.capability

    @property
    def option_questions(self) -> tuple[str, ...]:
        """Las preguntas cuyas opciones arma un builtin desde la entrada."""
        return tuple(q.id for q in self.spec.questions if q.options is not None)

    @staticmethod
    def positioned(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Cada ítem con su posición: `n` (desde 1) e `index` (desde 0)."""
        return [{**dict(item), "n": i + 1, "index": i} for i, item in enumerate(items)]

    def item_id(self, item: Mapping[str, Any]) -> str:
        """El id de la pregunta de un ítem ya posicionado."""
        assert self.spec.each is not None
        return render(self.spec.each.id, item)

    def each_questions(
        self, items: Iterable[Mapping[str, Any]], inp: Mapping[str, Any] | None = None, *, all_items: bool = False
    ) -> list[ItemQuestion]:
        """La pregunta de cada ítem que se pregunta (`each.when`), en orden.
        `all_items`: también las de los ítems que no se preguntan (para leer
        una respuesta que llegó igual)."""
        each = self.spec.each
        if each is None:
            return []
        env = {"inp": to_cel(dict(inp or {})), "consts": to_cel(dict(self.constants))}
        out: list[ItemQuestion] = []
        for item in self.positioned(items):
            env["item"] = to_cel(item)
            if not all_items and self.each_when is not None and not self._holds(self.each_when, env, item):
                continue
            template = next((text for when, text in self.each_texts if when is None or self._holds(when, env, item)), "")
            out.append(ItemQuestion(id=self.item_id(item), kind=each.kind, text=render(template, item),
                                    criteria=dict(each.criteria)))
        return out

    def _holds(self, condition: CompiledExpression, env: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
        try:
            return condition.evaluate(env) is True
        except ExpressionError as exc:
            logger.debug("decision_bundle.item_condition_failed", capability=self.name, item=item.get("n"),
                         error=str(exc)[:200])
            return False

    def _items_with_answers(self, items: Iterable[Mapping[str, Any]] | None, answers: Mapping[str, Any]) -> list[Any]:
        """Los ítems como los lee la tabla: campos, posición y la respuesta
        de Jev si llegó (`p`, o `choice` y `conf`)."""
        if self.spec.each is None:
            return []
        out: list[dict[str, Any]] = []
        for item in self.positioned(items or ()):
            answer = answers.get(self.item_id(item))
            if isinstance(answer, tuple):
                item["choice"], item["conf"] = str(answer[0]), float(answer[1])
            elif isinstance(answer, (int, float)) and not isinstance(answer, bool):
                item["p"] = float(answer)
            out.append(item)
        return to_cel(out)

    def questions_for(self, inp: Mapping[str, Any] | None) -> list[Question]:
        """Las preguntas que se hacen con esta entrada: las fijas y las
        condicionales cuya condición se cumple. Una condición que falla al
        evaluarse no se pregunta (sin respuesta, la tabla duda)."""
        conditions = dict(self.conditions)
        env = {"inp": to_cel(dict(inp or {})), "consts": to_cel(dict(self.constants))}
        out: list[Question] = []
        for question in self.spec.questions:
            condition = conditions.get(question.id)
            if condition is not None:
                try:
                    if condition.evaluate(env) is not True:
                        continue
                except ExpressionError as exc:
                    logger.debug("decision_bundle.question_condition_failed", capability=self.name,
                                 question=question.id, error=str(exc)[:200])
                    continue
            out.append(question)
        return out

    @property
    def thresholds(self) -> dict[str, float]:
        return dict(self.spec.thresholds)

    @property
    def type(self) -> ValueType:
        return self.value_type or parse_type(self.spec.value)

    def environment(
        self,
        answers: Mapping[str, Any],
        thresholds: Mapping[str, float] | None,
        rule: Any,
        inp: Mapping[str, Any] | None,
        options: Mapping[str, Mapping[str, Any]] | None = None,
        items: Iterable[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        kinds = {q.id: q.kind for q in self.spec.questions}
        p: dict[str, float] = {}
        choice: dict[str, str] = {}
        conf: dict[str, float] = {}
        normalized = normalize_answers(answers)
        for qid, answer in normalized.items():
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
            # Una pregunta con opciones sin opciones dadas = ninguna (no un error).
            "opt": to_cel({**{qid: {} for qid in self.option_questions},
                           **{qid: dict(values) for qid, values in (options or {}).items()}}),
            "vars": {},
            # Solo las capacidades con `items:` declaran la variable.
            **({"items": self._items_with_answers(items, normalized)} if self.spec.items is not None else {}),
        }

    def decide(
        self,
        *,
        answers: Mapping[str, Any],
        thresholds: Mapping[str, float] | None = None,
        rule: Any = None,
        inp: Mapping[str, Any] | None = None,
        options: Mapping[str, Mapping[str, Any]] | None = None,
        items: Iterable[Mapping[str, Any]] | None = None,
    ) -> Any:
        """El valor de la tabla (en la forma de Python de `value:`), o DOUBT.
        `options`: opción → valor de cada pregunta con opciones de la entrada.
        `items`: la lista de una capacidad que decide ítem por ítem."""
        env = self.environment(answers, thresholds, rule, inp, options, items)
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
