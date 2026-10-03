"""Las condiciones de un paquete: puerto + adaptador CEL.

Las filas de decisión (`when:`) son expresiones CEL compiladas contra un
entorno tipado: una variable no declarada, una comparación entre tipos
distintos o una condición que no devuelve bool no compilan. El puerto deja
cambiar de implementación (otro intérprete de CEL, un servicio) sin tocar
los paquetes; hoy el adaptador es `cel-expr-python`, la implementación
oficial de Google sobre cel-cpp (ADR-2026-10-01).
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Literal, Protocol

VarType = Literal[
    "bool", "string", "int", "double", "dyn", "map<string,double>", "map<string,string>", "map<string,dyn>",
    "map<string,map<string,dyn>>", "list<map<string,dyn>>",
]


class ExpressionError(Exception):
    """La condición no compila, o falló al evaluarse."""


class CompiledExpression(Protocol):
    @property
    def returns_bool(self) -> bool: ...

    @property
    def returns(self) -> str:
        """Lo que devuelve: bool | string | int | double | map | list | null | dyn."""
        ...

    def evaluate(self, data: Mapping[str, Any]) -> Any: ...


class ExpressionPort(Protocol):
    def compile(self, source: str, variables: Mapping[str, VarType]) -> CompiledExpression: ...


class _CelCompiled:
    def __init__(self, expression: Any, returns: str, error_type: Any) -> None:
        self._expression = expression
        self._returns = returns
        self._error_type = error_type

    @property
    def returns_bool(self) -> bool:
        return self._returns == "bool"

    @property
    def returns(self) -> str:
        return self._returns

    def evaluate(self, data: Mapping[str, Any]) -> Any:
        try:
            value = self._expression.eval(data=dict(data))
        except Exception as exc:  # noqa: BLE001 — cel-cpp lanza RuntimeError con el motivo
            raise ExpressionError(str(exc).splitlines()[0]) from None
        # Leer una llave que no está NO lanza: devuelve un valor de error.
        # Nunca se trata como false (la fila se saltaba y decidía la siguiente).
        if value.type() == self._error_type:
            raise ExpressionError(str(value.plain_value()).splitlines()[0])
        return value.plain_value()


def _cel_type(cel: Any, kind: VarType) -> Any:
    t = cel.Type
    return {
        "bool": t.BOOL,
        "string": t.STRING,
        "int": t.INT,
        "double": t.DOUBLE,
        "dyn": t.DYN,
        "map<string,double>": t.Map(t.STRING, t.DOUBLE),
        "map<string,string>": t.Map(t.STRING, t.STRING),
        "map<string,dyn>": t.Map(t.STRING, t.DYN),
        "map<string,map<string,dyn>>": t.Map(t.STRING, t.Map(t.STRING, t.DYN)),
        "list<map<string,dyn>>": t.List(t.Map(t.STRING, t.DYN)),
    }[kind]


#: Extensiones de CEL del motor: `strings` trae `join`, `lowerAscii`,
#: `trim`… (para unir las partes que quedan, p. ej. los párrafos del rescate).
_EXTENSIONS = """
extensions:
  - name: strings
"""


@lru_cache(maxsize=32)
def _cel_env(variables: tuple[tuple[str, VarType], ...]) -> Any:
    from cel_expr_python import cel

    config = cel.NewEnvConfigFromYaml(_EXTENSIONS)
    return cel.NewEnv(config=config, variables={name: _cel_type(cel, kind) for name, kind in variables})


class CelExpressions:
    """`ExpressionPort` con `cel-expr-python` (verifica tipos al compilar)."""

    name = "cel-expr-python"

    def compile(self, source: str, variables: Mapping[str, VarType]) -> CompiledExpression:
        from cel_expr_python import cel

        env = _cel_env(tuple(sorted(variables.items())))
        try:
            expression = env.compile(source)
        except Exception as exc:  # noqa: BLE001 — el motivo viene en el mensaje
            raise ExpressionError(_compile_message(str(exc))) from None
        return _CelCompiled(expression, _returns(str(expression.return_type())), cel.Type.ERROR)


def _returns(name: str) -> str:
    name = name.upper()
    for prefix, kind in (("BOOL", "bool"), ("STRING", "string"), ("INT", "int"), ("DOUBLE", "double"),
                         ("MAP", "map"), ("LIST", "list"), ("NULL", "null")):
        if name.startswith(prefix):
            return kind
    return "dyn"


def _compile_message(raw: str) -> str:
    """El primer renglón útil del error de cel-cpp, sin el prefijo de gRPC."""
    first = raw.splitlines()[0] if raw else "no compila"
    return first.removeprefix("INVALID_ARGUMENT: ").removeprefix("ERROR: ")
