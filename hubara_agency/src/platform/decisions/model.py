"""Forma de un paquete de decisión (PAQUETES_DE_DECISION.md §5).

Modelos Pydantic estrictos: una llave que no existe, un tipo equivocado o un
campo que falta rechazan el archivo (código DB001 del certificador). Los
modelos también generan el esquema JSON que usa el editor para autocompletar.

  Bundle      `bundle.yaml`: id, versión, contrato del motor, capacidades.
  Capability  `capabilities/<nombre>.yaml`: regla, estado, preguntas,
              umbrales, tabla de decisión, piso, comparador y ejemplos.
  Catalog     `builtins.yaml` del motor: lo que un paquete puede pedir por
              nombre (reglas, constructores de estado, pisos, comparadores),
              con su firma. Es el "header" entre motor y paquetes.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ValueType = Literal["bool", "string"]
ParamType = Literal["str", "int", "float", "bool"]
QuestionKind = Literal["noul", "choice"]
BuiltinKind = Literal["rule", "state", "floor", "same"]

#: La duda: la fila no decide y decide la regla (`then: doubt`).
DOUBT_WORD = "doubt"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BuiltinRef(_Strict):
    """Un builtin del motor pedido por nombre, con sus parámetros (`with`)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    builtin: str = Field(min_length=1)
    params: dict[str, str | int | float | bool] = Field(default_factory=dict, alias="with")


class Question(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    kind: QuestionKind
    text: str = Field(min_length=1)
    criteria: dict[str, str]

    @field_validator("criteria", mode="before")
    @classmethod
    def _yaml_booleans(cls, value: Any) -> Any:
        # `{true: sí, false: no}` sin comillas llega como llaves booleanas.
        if isinstance(value, dict):
            return {str(k).lower() if isinstance(k, bool) else k: v for k, v in value.items()}
        return value

    @model_validator(mode="after")
    def _criteria_shape(self) -> Question:
        if self.kind == "noul" and set(self.criteria) != {"true", "false"}:
            raise ValueError("una pregunta noul lleva criteria con exactamente las llaves true y false")
        if self.kind == "choice" and len(self.criteria) < 2:
            raise ValueError("una pregunta choice lleva al menos dos opciones en criteria")
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
    """Un caso que el certificador corre: con estas respuestas (y esta regla),
    la tabla debe dar `expect` (o `doubt`)."""

    answers: dict[str, float | ChoiceAnswer] = Field(default_factory=dict)
    rule: Any = None
    expect: Any


class Capability(_Strict):
    capability: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    value: ValueType
    options: list[str] | None = None
    input: str = Field(min_length=1)
    rule: BuiltinRef
    state: BuiltinRef | None
    questions: list[Question] = Field(min_length=1)
    thresholds: dict[str, float] = Field(default_factory=dict)
    decide: list[WhenRow | OtherwiseRow] = Field(min_length=1)
    floor: BuiltinRef
    same: BuiltinRef
    examples: list[Example] = Field(min_length=1)

    @model_validator(mode="after")
    def _options_only_for_strings(self) -> Capability:
        if self.options is not None and self.value != "string":
            raise ValueError("options solo aplica a value: string")
        return self


class Bundle(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
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
    value: ValueType | None = None
    input: str | None = None
    params: dict[str, ParamType] = Field(default_factory=dict)
    doc: str = ""


class Catalog(_Strict):
    engine_contract: int = Field(ge=1)
    inputs: list[str] = Field(min_length=1)
    builtins: dict[str, BuiltinSpec]
    #: capacidad → piso que el paquete NO puede cambiar (p. ej. la baja legal).
    required_floors: dict[str, str] = Field(default_factory=dict)
