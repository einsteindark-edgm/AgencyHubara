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

VarType = Literal["bool", "string", "double", "map<string,double>", "map<string,string>"]


class ExpressionError(Exception):
    """La condición no compila, o falló al evaluarse."""


class CompiledExpression(Protocol):
    @property
    def returns_bool(self) -> bool: ...

    def evaluate(self, data: Mapping[str, Any]) -> Any: ...


class ExpressionPort(Protocol):
    def compile(self, source: str, variables: Mapping[str, VarType]) -> CompiledExpression: ...


class _CelCompiled:
    def __init__(self, expression: Any, returns_bool: bool) -> None:
        self._expression = expression
        self._returns_bool = returns_bool

    @property
    def returns_bool(self) -> bool:
        return self._returns_bool

    def evaluate(self, data: Mapping[str, Any]) -> Any:
        try:
            return self._expression.eval(data=dict(data)).value()
        except Exception as exc:  # noqa: BLE001 — cel-cpp lanza RuntimeError con el motivo
            raise ExpressionError(str(exc).splitlines()[0]) from None


def _cel_type(cel: Any, kind: VarType) -> Any:
    t = cel.Type
    return {
        "bool": t.BOOL,
        "string": t.STRING,
        "double": t.DOUBLE,
        "map<string,double>": t.Map(t.STRING, t.DOUBLE),
        "map<string,string>": t.Map(t.STRING, t.STRING),
    }[kind]


@lru_cache(maxsize=16)
def _cel_env(variables: tuple[tuple[str, VarType], ...]) -> Any:
    from cel_expr_python import cel

    return cel.NewEnv(variables={name: _cel_type(cel, kind) for name, kind in variables})


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
        return _CelCompiled(expression, expression.return_type() == cel.Type.BOOL)


def _compile_message(raw: str) -> str:
    """El primer renglón útil del error de cel-cpp, sin el prefijo de gRPC."""
    first = raw.splitlines()[0] if raw else "no compila"
    return first.removeprefix("INVALID_ARGUMENT: ").removeprefix("ERROR: ")
