"""El turno de un paquete (`turn.yaml`, PAQUETES_DE_DECISION.md F7).

Antes del turno Jev contesta la ráfaga ① (qué asuntos plantea el cliente,
qué le preguntó el asesor, qué responde); antes de enviar, la verificación
③ (¿la respuesta atiende cada asunto?). El `turn.yaml` del paquete trae lo
que eso decide, como datos certificados:

  policy         la política (código del plugin) que arma el turno con estas tablas
  thresholds     sus umbrales (los que declara el catálogo, ni uno más)
  questionnaire  la ráfaga ①: asuntos, preguntas (fijas, por asunto, por
                 mensaje; con `when` sobre hechos del turno), la pregunta de ③
                 y los textos del `state`. Viaja tal cual: lo interpreta el plugin
  coverage       ② qué atiende cada asunto dentro del turno (tools, palabras, cualquier texto)
  reading        la nota según lo que preguntó el asesor y lo que responde el cliente
  contract       asunto → tools que lo resuelven, como FILAS: la primera del
                 asunto que se cumple decide; `any_of: []` = no pide tool
  verify_decide  ③ por asunto: covered | missing | doubt. El motor junta las
                 bandas: cualquier missing → complemento; cualquier doubt → pendiente
  guide          la guía de etapas (asuntos que se eligen, etapas sin venta, …)
  examples       casos del contrato y de ③ que el certificador corre

Las condiciones son CEL: el contrato lee `p` (la probabilidad de cada
pregunta sí/no; una respuesta sin probabilidad vale 0), `th`, `inp` (lo que
declara el catálogo, p. ej. la etapa) y `dom`; ③ lee `item` (`topic`,
`msg` y `p` si Jev contestó), `th` y `dom`. Una condición que falla al
evaluarse no tumba el turno: en el contrato, el asunto no pide tool; en ③,
duda.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import structlog
from pydantic import Discriminator, Field, Tag, field_validator, model_validator

from src.platform.decisions.engine import CompiledRow
from src.platform.decisions.expressions import CompiledExpression, ExpressionError
from src.platform.decisions.model import FactValue, OtherwiseRow, QuestionKind, WhenRow, _Strict

logger = structlog.get_logger()

#: Las bandas de ③ por asunto.
BANDS = ("covered", "missing", "doubt")
#: Lo que decide ③ para el turno.
VERIFY_DECISIONS = ("send", "complement", "pending")
#: Lo que trae cada asunto en las filas de ③ (`item.campo`).
VERIFY_ITEM = ("topic", "msg", "p")

_QID = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$"
_TEMPLATE_QID = r"^[a-z][a-z0-9_]*(\.[a-z0-9_{}]+)+$"

Facts = dict[str, FactValue | list[FactValue]]


class _WithCriteria(_Strict):
    """Una pregunta con `kind` y `criteria` (sí/no u opciones)."""

    @field_validator("criteria", mode="before", check_fields=False)
    @classmethod
    def _yaml_booleans(cls, value: Any) -> Any:
        # `{true: sí, false: no}` sin comillas llega como llaves booleanas.
        if isinstance(value, dict):
            return {str(k).lower() if isinstance(k, bool) else k: v for k, v in value.items()}
        return value

    @model_validator(mode="after")
    def _criteria_shape(self) -> Any:
        kind, criteria = getattr(self, "kind"), getattr(self, "criteria")
        if kind == "noul" and set(criteria) != {"true", "false"}:
            raise ValueError("una pregunta noul lleva criteria con exactamente las llaves true y false")
        if kind == "choice" and len(criteria) < 2:
            raise ValueError("una pregunta choice lleva al menos dos opciones en criteria")
        return self


class TurnTopic(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    hint: str = ""


class BurstQuestion(_WithCriteria):
    """Una pregunta fija de la ráfaga; con `when`, solo si los hechos del
    turno coinciden (`{hecho: valor}` o `{hecho: [valores]}`)."""

    id: str = Field(pattern=_QID)
    kind: QuestionKind
    text: str = Field(min_length=1)
    criteria: dict[str, str]
    when: Facts | None = None


class EachTopic(_WithCriteria):
    """La pregunta de cada asunto: `{topic}`, `{label}` y `{hint}`."""

    id: str = Field(pattern=_TEMPLATE_QID)
    kind: QuestionKind
    text: str = Field(min_length=1)
    criteria: dict[str, str]
    when: Facts | None = None


class EachMessageCriteria(_Strict):
    #: Las opciones son los asuntos, por su etiqueta.
    from_topics: Literal["label"]
    #: Opciones fijas al final (p. ej. `ninguno`).
    extra: dict[str, str] = Field(default_factory=dict)


class EachMessage(_Strict):
    """La pregunta de cada mensaje de la ráfaga: `{k}` (desde 1)."""

    id: str = Field(pattern=_TEMPLATE_QID)
    kind: Literal["choice"]
    text: str = Field(min_length=1)
    criteria: EachMessageCriteria
    when: Facts | None = None


class EachTopicEntry(_Strict):
    each_topic: EachTopic


class EachMessageEntry(_Strict):
    each_message: EachMessage


def _question_tag(value: Any) -> str:
    if isinstance(value, Mapping) and "each_topic" in value:
        return "EachTopicEntry"
    if isinstance(value, Mapping) and "each_message" in value:
        return "EachMessageEntry"
    return "BurstQuestion"


QuestionEntry = Annotated[
    Annotated[BurstQuestion, Tag("BurstQuestion")]
    | Annotated[EachTopicEntry, Tag("EachTopicEntry")]
    | Annotated[EachMessageEntry, Tag("EachMessageEntry")],
    Discriminator(_question_tag),
]


class VerifySpec(_WithCriteria):
    """La pregunta de ③ por asunto: `{label}` y `{where}` (`where_msg` con
    `{msg}` si se sabe el mensaje; si no, `where_none`)."""

    id: str = Field(pattern=_TEMPLATE_QID)
    kind: Literal["noul"]
    text: str = Field(min_length=1)
    where_msg: str = Field(min_length=1)
    where_none: str = Field(min_length=1)
    criteria: dict[str, str]


class StateSections(_Strict):
    context: str = Field(min_length=1)
    facts: str = Field(min_length=1)
    turn: str = Field(min_length=1)
    #: `{text}`: el mensaje que el cliente cita.
    quoted: str = Field(min_length=1)


class BurstState(_Strict):
    sections: StateSections
    #: Cada mensaje del turno: `{k}`, `{offset}` y `{text}`.
    message: str = Field(min_length=1)
    #: `{pending}`: los asuntos que quedaron sin responder.
    pending: str = Field(min_length=1)


class ReplyState(_Strict):
    header: str = Field(min_length=1)
    empty: str = Field(min_length=1)
    #: `{components}`: las tarjetas que también recibe el cliente.
    components: str = Field(min_length=1)


class TurnQuestionnaire(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    topics: list[TurnTopic] = Field(min_length=1)
    questions: list[QuestionEntry] = Field(min_length=1)
    verify: VerifySpec
    state: BurstState
    reply_state: ReplyState


class CoverageRule(_Strict):
    tools: list[str] = Field(default_factory=list)
    #: Palabras del texto que ve el cliente (sin tildes) que lo atienden.
    words: list[str] = Field(default_factory=list)
    #: Cualquier texto lo atiende (p. ej. un aplazamiento).
    any_text: bool = False


class Reading(_Strict):
    #: lo que preguntó el asesor → lo que responde el cliente → la nota.
    answered: dict[str, dict[str, str]] = Field(default_factory=dict)
    #: lo que preguntó el asesor → la nota, responda lo que responda.
    any: dict[str, str] = Field(default_factory=dict)


class ContractRow(_Strict):
    topic: str = Field(min_length=1)
    #: Condición CEL (lee p, th, inp, dom). Sin ella, la fila siempre se cumple.
    when: str | None = Field(default=None, min_length=1)
    #: Las tools que lo resuelven (cualquiera de ellas). `[]` = no pide tool.
    any_of: list[str]
    #: La nota que nombra la tool que falta (la segunda puerta del turno).
    nudge: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _nudge_with_tools(self) -> ContractRow:
        if self.any_of and self.nudge is None:
            raise ValueError("una fila que pide tools lleva su `nudge` (la nota que nombra la que falta)")
        if not self.any_of and self.nudge is not None:
            raise ValueError("una fila sin tools (`any_of: []`) no lleva `nudge`")
        return self


class Guide(_Strict):
    #: Asuntos que el cliente puede estar ELIGIENDO en vez de preguntando.
    choice_topics: list[str] = Field(default_factory=list)
    #: Etapas sin una venta en curso (ahí el que solo agradece no se empuja a comprar).
    no_sale_stages: list[str] = Field(default_factory=list)
    #: Turnos seguidos sin un dato nuevo para reforzar el paso.
    stagnant_turns: int = Field(ge=1)
    #: Cómo se nombra cada dato en la guía.
    slot_labels: dict[str, str] = Field(default_factory=dict)


class TopicRef(_Strict):
    topic: str = Field(min_length=1)
    msg: int | None = None


class Requirement(_Strict):
    topic: str = Field(min_length=1)
    any_of: list[str]


class ContractExample(_Strict):
    topics: list[str] = Field(min_length=1)
    answers: dict[str, float] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    expect: list[Requirement]


class VerifyExpect(_Strict):
    decision: Literal["send", "complement", "pending"]
    missing: list[str] = Field(default_factory=list)


class VerifyExample(_Strict):
    topics: list[str | TopicRef] = Field(min_length=1)
    answers: dict[str, float] = Field(default_factory=dict)
    expect: VerifyExpect


class TurnExamples(_Strict):
    contract: list[ContractExample] = Field(min_length=1)
    verify: list[VerifyExample] = Field(min_length=1)


class Turn(_Strict):
    policy: str = Field(min_length=1)
    thresholds: dict[str, float]
    questionnaire: TurnQuestionnaire
    coverage: dict[str, CoverageRule]
    reading: Reading = Field(default_factory=Reading)
    contract: list[ContractRow] = Field(default_factory=list)
    verify_decide: list[WhenRow | OtherwiseRow] = Field(min_length=1)
    guide: Guide
    examples: TurnExamples


# ── el turno compilado ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class TurnVerify:
    """③ para el turno: send | complement | pending, los asuntos que faltan
    (o en duda) y la probabilidad de cada asunto que Jev contestó."""

    decision: str
    missing: tuple[str, ...] = ()
    covered: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class CompiledContractRow:
    topic: str
    when: CompiledExpression | None
    any_of: tuple[str, ...]
    nudge: str | None


@dataclass(frozen=True)
class CompiledTurn:
    spec: Turn
    #: El cuestionario tal cual lo escribió el paquete (lo interpreta el plugin).
    questionnaire: Mapping[str, Any]
    contract_rows: tuple[CompiledContractRow, ...]
    verify_rows: tuple[CompiledRow, ...]
    bundle: str = ""
    domain: Mapping[str, Any] = field(default_factory=dict)

    @property
    def policy(self) -> str:
        return self.spec.policy

    @property
    def thresholds(self) -> dict[str, float]:
        return dict(self.spec.thresholds)

    @property
    def coverage(self) -> dict[str, dict[str, Any]]:
        return {topic: rule.model_dump() for topic, rule in self.spec.coverage.items()}

    @property
    def reading_answered(self) -> dict[tuple[str, str], str]:
        return {(asked, answer): note for asked, notes in self.spec.reading.answered.items() for answer, note in notes.items()}

    @property
    def reading_any(self) -> dict[str, str]:
        return dict(self.spec.reading.any)

    @property
    def guide(self) -> Guide:
        return self.spec.guide

    def required(self, topics: Sequence[str], *, p: Mapping[str, float], inp: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Las tools que pide cada asunto, en el orden de los asuntos: la
        primera fila del asunto que se cumple decide."""
        env = {"p": dict(p), "th": self.thresholds, "inp": dict(inp), "dom": dict(self.domain)}
        out: list[dict[str, Any]] = []
        for topic in topics:
            for row in self.contract_rows:
                if row.topic != topic:
                    continue
                if row.when is not None:
                    try:
                        holds = row.when.evaluate(env)
                    except ExpressionError as exc:
                        logger.warning("decisions.turn.contract_error", bundle=self.bundle, topic=topic, error=str(exc))
                        break
                    if holds is not True:
                        continue
                if row.any_of:
                    out.append({"topic": topic, "any_of": list(row.any_of), "nudge": row.nudge})
                break
        return out

    def band(self, topic: str, *, msg: int | None, p: float | None) -> str:
        """③ de un asunto: covered | missing | doubt."""
        item: dict[str, Any] = {"topic": topic, "msg": msg}
        if p is not None:
            item["p"] = float(p)
        env = {"item": item, "th": self.thresholds, "dom": dict(self.domain)}
        for row in self.verify_rows:
            if row.when is not None:
                try:
                    holds = row.when.evaluate(env)
                except ExpressionError as exc:
                    logger.warning("decisions.turn.verify_error", bundle=self.bundle, topic=topic, error=str(exc))
                    return "doubt"
                if holds is not True:
                    continue
            return str(row.then)
        return "doubt"

    def verify(self, topics: Sequence[str | tuple[str, int | None]], *, p: Mapping[str, float]) -> TurnVerify:
        """③ del turno: la banda de cada asunto (con la respuesta de su
        pregunta, `p`) y su suma."""
        covered: dict[str, float] = {}
        missing: list[str] = []
        doubts: list[str] = []
        for entry in topics:
            topic, msg = (entry, None) if isinstance(entry, str) else entry
            value = p.get(self.spec.questionnaire.verify.id.format(topic=topic))
            if value is not None:
                covered[topic] = float(value)
            band = self.band(topic, msg=msg, p=value)
            if band == "missing":
                missing.append(topic)
            elif band == "doubt":
                doubts.append(topic)
        if missing:
            return TurnVerify("complement", tuple(missing), covered)
        if doubts:
            return TurnVerify("pending", tuple(doubts), covered)
        return TurnVerify("send", (), covered)


__all__ = ["BANDS", "VERIFY_DECISIONS", "VERIFY_ITEM", "CompiledTurn", "Turn", "TurnVerify"]
