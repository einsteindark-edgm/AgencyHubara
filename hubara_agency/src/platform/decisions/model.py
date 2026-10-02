"""Forma de un paquete de decisión (PAQUETES_DE_DECISION.md §5).

Modelos Pydantic estrictos: una llave que no existe, un tipo equivocado o un
campo que falta rechazan el archivo (código DB001 del certificador). Los
modelos también generan el esquema JSON que usa el editor para autocompletar.

  Bundle      `bundle.yaml`: id, versión, contrato del motor, capacidades.
  Capability  `capabilities/<nombre>.yaml`: regla, estado, vista, preguntas
              (fijas, condicionales o con opciones de la entrada), umbrales,
              tabla de decisión, piso, comparador y ejemplos.
  Catalog     `builtins.yaml` del motor: lo que un paquete puede pedir por
              nombre (reglas, constructores de estado, opciones, vistas,
              pisos, comparadores), con su firma. Es el "header" entre motor
              y paquetes.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.platform.decisions.types import TypeSyntaxError, parse_type

ParamType = Literal["str", "int", "float", "bool"]
QuestionKind = Literal["noul", "choice"]
BuiltinKind = Literal["rule", "state", "options", "view", "items", "floor", "same"]

#: La duda: la fila no decide y decide la regla (`then: doubt`).
DOUBT_WORD = "doubt"
#: El id de un paquete (= su carpeta): el mismo patrón en el modelo, en el
#: brazo del laboratorio (`B@<id>`) y en Terraform (`decisions_bundle`).
BUNDLE_ID = r"[a-z][a-z0-9-]{0,39}"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def yaml_booleans(data: Any) -> Any:
    """Las llaves de `criteria` que YAML 1.1 lee como booleanas.

    En una pregunta sí/no, `{true: …, false: …}` sin comillas es la forma
    esperada: se normaliza a "true"/"false". En una de opciones es un error:
    `no:` sin comillas es `false` y la opción «no» se volvería «false» (la
    fila `== 'no'` quedaría muerta sin que nada lo diga)."""
    if not isinstance(data, dict) or not isinstance(data.get("criteria"), dict):
        return data
    criteria = data["criteria"]
    if not any(isinstance(k, bool) for k in criteria):
        return data
    if data.get("kind") != "noul":
        bad = sorted(str(k).lower() for k in criteria if isinstance(k, bool))
        raise ValueError(f"opciones sin comillas que YAML lee como booleanas {bad}: escribe por ejemplo \"no\": …")
    return {**data, "criteria": {str(k).lower() if isinstance(k, bool) else k: v for k, v in criteria.items()}}


class BuiltinRef(_Strict):
    """Un builtin del motor pedido por nombre, con sus parámetros (`with`)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    builtin: str = Field(min_length=1)
    params: dict[str, str | int | float | bool] = Field(default_factory=dict, alias="with")


class Question(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    kind: QuestionKind
    text: str = Field(min_length=1)
    #: Opciones fijas. Con `options`, van DESPUÉS de las que arma el builtin
    #: (p. ej. `ambiguo` y `ninguno`).
    criteria: dict[str, str] = Field(default_factory=dict)
    #: Builtin (clase `options`) que arma opciones desde la entrada: cada una
    #: con su etiqueta (lo que lee Jev) y su valor (`opt['<id>'][<opción>]`).
    options: BuiltinRef | None = None
    #: Se pregunta solo si esta condición CEL sobre la entrada se cumple
    #: (lee `inp` y `consts`; todavía no hay respuestas).
    when: str | None = Field(default=None, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _yaml_booleans(cls, data: Any) -> Any:
        return yaml_booleans(data)

    @model_validator(mode="after")
    def _criteria_shape(self) -> Question:
        if self.kind == "noul" and self.options is not None:
            raise ValueError("options (opciones de la entrada) es solo para una pregunta choice")
        if self.kind == "noul" and set(self.criteria) != {"true", "false"}:
            raise ValueError("una pregunta noul lleva criteria con exactamente las llaves true y false")
        if self.kind == "choice" and self.options is None and len(self.criteria) < 2:
            raise ValueError("una pregunta choice lleva al menos dos opciones en criteria (o options)")
        return self


class TextVariant(_Strict):
    when: str = Field(min_length=1)
    text: str = Field(min_length=1)


class TextOtherwise(_Strict):
    otherwise: str = Field(min_length=1)


class Each(_Strict):
    """La pregunta de cada ítem (`items:`). `id` y `text` son plantillas:
    `{n}` (posición desde 1), `{index}` (desde 0) y los campos del ítem."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_{}]+)+$")
    kind: QuestionKind
    #: Un texto, o variantes por condición sobre el ítem (la primera que se cumple).
    text: str | list[TextVariant | TextOtherwise]
    criteria: dict[str, str]
    #: Se pregunta solo por los ítems donde se cumple (lee `item`, `inp`, `consts`).
    when: str | None = Field(default=None, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _yaml_booleans(cls, data: Any) -> Any:
        return yaml_booleans(data)

    @model_validator(mode="after")
    def _shape(self) -> Each:
        if self.kind == "noul" and set(self.criteria) != {"true", "false"}:
            raise ValueError("una pregunta noul lleva criteria con exactamente las llaves true y false")
        if self.kind == "choice" and len(self.criteria) < 2:
            raise ValueError("una pregunta choice lleva al menos dos opciones en criteria")
        if isinstance(self.text, list):
            if not self.text or not isinstance(self.text[-1], TextOtherwise):
                raise ValueError("las variantes del texto terminan con `otherwise`")
            if any(isinstance(v, TextOtherwise) for v in self.text[:-1]):
                raise ValueError("`otherwise` va al final de las variantes del texto")
        return self


class WhenRow(_Strict):
    when: str = Field(min_length=1)
    then: Any


class OtherwiseRow(_Strict):
    otherwise: Any


class ChoiceAnswer(_Strict):
    choice: str
    p: float


class Example(_Strict):
    """Un caso que el certificador corre: con estas respuestas (y esta regla
    y esta entrada), la tabla debe dar `expect` (o `doubt`)."""

    answers: dict[str, float | ChoiceAnswer] = Field(default_factory=dict)
    rule: Any = None
    input: dict[str, Any] = Field(default_factory=dict)
    #: Los ítems (capacidades con `items:`), cada uno con su respuesta si la
    #: hubo: `p` (sí/no) o `choice` y `conf` (opciones).
    items: list[dict[str, Any]] | None = None
    #: Las opciones de la entrada de cada pregunta con `options` (opción → valor).
    options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    expect: Any


def _type_text(value: str) -> str:
    try:
        parse_type(value)
    except TypeSyntaxError as exc:
        raise ValueError(str(exc)) from None
    return value


class Capability(_Strict):
    capability: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    #: Tipo del valor (gramática en `types.py`): bool, string, {cantidad: int?}…
    value: str
    options: list[str] | None = None
    #: La capacidad cuyo interruptor comparte (una variante: la misma
    #: decisión preguntada de otra forma). Default: ella misma.
    control: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    input: str = Field(min_length=1)
    #: Builtin (clase `view`) que deriva campos de la entrada (`inp.campo`).
    view: BuiltinRef | None = None
    rule: BuiltinRef
    state: BuiltinRef | None
    questions: list[Question] = Field(default_factory=list)
    #: Builtin (clase `items`) que arma la lista sobre la que se decide ítem por ítem.
    items: BuiltinRef | None = None
    #: La pregunta de cada ítem.
    each: Each | None = None
    thresholds: dict[str, float] = Field(default_factory=dict)
    #: Cálculos intermedios en orden (CEL); se leen como `vars.nombre`.
    vars: dict[str, str] = Field(default_factory=dict)
    decide: list[WhenRow | OtherwiseRow] = Field(min_length=1)
    floor: BuiltinRef
    same: BuiltinRef
    examples: list[Example] = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def _value_type(cls, value: str) -> str:
        return _type_text(value)

    @model_validator(mode="after")
    def _options_only_for_strings(self) -> Capability:
        if self.options is not None and parse_type(self.value).kind != "string":
            raise ValueError("options solo aplica a un value string")
        if self.each is not None and self.items is None:
            raise ValueError("each (la pregunta por ítem) necesita items (la lista)")
        if not self.questions and self.each is None:
            raise ValueError("una capacidad lleva questions, o items + each")
        return self


class Bundle(_Strict):
    id: str = Field(pattern=f"^{BUNDLE_ID}$")
    version: int = Field(ge=1)
    engine_contract: int = Field(ge=1)
    oracle: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)

    @field_validator("capabilities")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("capacidad repetida")
        return value


class BuiltinSpec(_Strict):
    kind: BuiltinKind
    value: str | None = None
    input: str | None = None
    params: dict[str, ParamType] = Field(default_factory=dict)
    #: Los campos que deriva una vista, o los de cada ítem (nombre → tipo).
    fields: dict[str, str] = Field(default_factory=dict)
    #: Campos de cada ítem que lo identifican (valores del código, no texto
    #: del cliente): los únicos que puede llevar el id de la pregunta por ítem
    #: además de su posición (`{n}`, `{index}`).
    keys: list[str] = Field(default_factory=list)
    #: Un estado que recibe los ítems (`items=`): el de una capacidad por ítems
    #: los recibe siempre.
    takes_items: bool = False
    doc: str = ""

    @model_validator(mode="after")
    def _fields_only_for_views(self) -> BuiltinSpec:
        if self.fields and self.kind not in ("view", "items"):
            raise ValueError("fields es solo para un builtin de clase view o items")
        if self.keys and (self.kind != "items" or not set(self.keys) <= set(self.fields)):
            raise ValueError("keys son campos de los ítems (`fields`) de un builtin de clase items")
        if self.takes_items and self.kind != "state":
            raise ValueError("takes_items es solo para un builtin de clase state")
        for name, text in self.fields.items():
            try:
                parse_type(text)
            except TypeSyntaxError as exc:
                raise ValueError(f"fields.{name}: {exc}") from None
        return self


class ConstantSpec(_Strict):
    type: str
    value: Any

    @field_validator("type")
    @classmethod
    def _constant_type(cls, value: str) -> str:
        return _type_text(value)


#: El valor de un hecho del turno (lo que filtra una pregunta de la ráfaga).
FactValue = str | int | float | bool | None


class TurnPolicySpec(_Strict):
    """Una política del turno (código) que lee un `turn.yaml`: los umbrales y
    las preguntas que lee. El cuestionario tiene que hacerlas."""

    thresholds: list[str] = Field(min_length=1)
    #: Preguntas fijas que lee la política, por clase (noul | choice).
    reads: dict[QuestionKind, list[str]] = Field(default_factory=dict)
    #: El id de la pregunta de cada asunto (`each_topic`), de cada mensaje
    #: (`each_message`) y de la verificación ③ (`verify`).
    topic_question: str = Field(min_length=1)
    message_question: str = Field(min_length=1)
    verify_question: str = Field(min_length=1)
    #: Las preguntas de la lectura del hilo: qué preguntó el asesor y qué
    #: responde el cliente (las llaves de `reading` son sus opciones).
    reading: dict[Literal["asked", "answer"], str] = Field(default_factory=dict)


class TurnCatalog(_Strict):
    """El vocabulario del turno (`turn:` del catálogo): lo que un `turn.yaml`
    puede nombrar. Lo que no está aquí no compila (DB015)."""

    policies: dict[str, TurnPolicySpec] = Field(min_length=1)
    #: Hechos del turno que filtran las preguntas (`when`) → sus valores posibles.
    facts: dict[str, list[FactValue]] = Field(default_factory=dict)
    #: Lo que las filas del contrato leen como `inp.campo` (nombre → tipo).
    inputs: dict[str, str] = Field(default_factory=dict)
    stages: list[str] = Field(default_factory=list)
    slots: list[str] = Field(default_factory=list)
    #: Las tools del agente que el contrato y la regla ② pueden nombrar.
    tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _input_types(self) -> TurnCatalog:
        for name, text in self.inputs.items():
            try:
                parse_type(text)
            except TypeSyntaxError as exc:
                raise ValueError(f"inputs.{name}: {exc}") from None
        return self


class CapabilityAbout(_Strict):
    """Qué resuelve una decisión y en qué parte del software actúa, en
    palabras del operador (Calidad LLM → «Motor de decisiones», 2026-10-02)."""

    name: str = Field(min_length=1)
    #: Las partes del software (`places` del catálogo) donde actúa.
    where: list[str] = Field(min_length=1)
    solves: str = Field(min_length=1)


class Catalog(_Strict):
    engine_contract: int = Field(ge=1)
    #: Tipos de entrada y sus campos (nombre → tipo), lo que una condición
    #: puede leer como `inp.campo`.
    inputs: dict[str, dict[str, str]] = Field(min_length=1)
    builtins: dict[str, BuiltinSpec]
    #: Constantes del motor que una condición lee como `consts.NOMBRE`.
    constants: dict[str, ConstantSpec] = Field(default_factory=dict)
    #: capacidad → piso que el paquete NO puede cambiar (p. ej. la baja legal).
    required_floors: dict[str, str] = Field(default_factory=dict)
    #: capacidad → regla que el paquete NO puede cambiar (la baja legal: si
    #: Jev duda o cae, decide esta regla).
    required_rules: dict[str, str] = Field(default_factory=dict)
    #: Las capacidades que el código del motor le pide a un paquete: tiene que
    #: traerlas todas (si no, se corría la clase de Python: la inteligencia de
    #: otra tienda).
    capabilities: list[str] = Field(default_factory=list)
    #: El plugin le pasa los ítems (`items=`) al estado de una capacidad por
    #: ítems: entonces ese estado tiene que recibirlos (`takes_items`).
    items_to_state: bool = False
    #: El dominio de la tienda que trae cada paquete (`domain.yaml`): campo →
    #: tipo, o una sección (campo → tipo). Lo leen las condiciones (`dom.x`)
    #: y el código del agente (el vocabulario de sus herramientas).
    domain: dict[str, str | dict[str, str]] = Field(default_factory=dict)
    #: El vocabulario del turno (F7). Con él, cada paquete trae su `turn.yaml`.
    turn: TurnCatalog | None = None
    #: Las partes del software donde el motor decide algo (id → nombre en
    #: palabras del operador), en el orden de una conversación.
    places: dict[str, str] = Field(default_factory=dict)
    #: capacidad → qué resuelve y dónde actúa. Cada capacidad que el código
    #: pide (`capabilities`) trae la suya: Calidad LLM la muestra (DB003).
    about: dict[str, CapabilityAbout] = Field(default_factory=dict)

    @field_validator("inputs", mode="before")
    @classmethod
    def _inputs_without_fields(cls, value: Any) -> Any:
        # `inputs: [Inbound]` = tipos sin campos legibles desde las condiciones.
        return {name: {} for name in value} if isinstance(value, list) else value

    @model_validator(mode="after")
    def _types_and_constants(self) -> Catalog:
        for name, fields in self.inputs.items():
            for field_name, text in fields.items():
                try:
                    parse_type(text)
                except TypeSyntaxError as exc:
                    raise ValueError(f"inputs.{name}.{field_name}: {exc}") from None
        from src.platform.decisions.types import conforms

        for name, constant in self.constants.items():
            if not conforms(constant.value, parse_type(constant.type)):
                raise ValueError(f"constants.{name}: {constant.value!r} no es {constant.type}")
        for name, declared in self.domain.items():
            for field_name, text in (declared.items() if isinstance(declared, dict) else [(None, declared)]):
                try:
                    parse_type(text)
                except TypeSyntaxError as exc:
                    where = f"domain.{name}" + (f".{field_name}" if field_name else "")
                    raise ValueError(f"{where}: {exc}") from None
        return self
