"""Los tipos de valor de una capacidad (`value:` en el YAML).

Gramática (la escribe quien arma el paquete; el certificador la parsea):

    tipo     := base ['?']                    '?' = puede ser null
    base     := bool | string | int | double | any
              | list<tipo> | tuple<tipo>      tuple = tupla de Python (inmutable)
              | '{' campo (',' campo)* '}'    registro (dict con esas llaves)
    campo    := nombre ':' tipo

Ejemplos: `bool`, `tuple<string>`, `{cantidad: int?}`,
`{deferral: {kind: string, until_ms: int}?, courtesy: bool}`.

`conforms` dice si un valor es del tipo (los `then:` literales y los
`expect:` de los ejemplos); `convert` lo lleva a la forma de Python que
esperan los consumidores (listas de CEL → tuplas donde el tipo dice tuple).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_PRIMITIVES = ("bool", "string", "int", "double", "any")
_TOKEN = re.compile(r"\s*(?:([A-Za-z_][A-Za-z0-9_]*)|(.))")


@dataclass(frozen=True)
class ValueType:
    kind: str  # bool | string | int | double | any | list | tuple | record
    nullable: bool = False
    item: ValueType | None = None
    fields: tuple[tuple[str, ValueType], ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        if self.kind in ("list", "tuple"):
            base = f"{self.kind}<{self.item}>"
        elif self.kind == "record":
            base = "{" + ", ".join(f"{n}: {t}" for n, t in self.fields) + "}"
        else:
            base = self.kind
        return base + ("?" if self.nullable else "")

    @property
    def cel_var(self) -> str:
        """Cómo se declara en CEL una variable de este tipo (`rule`)."""
        if self.nullable:
            return "dyn"
        return {"bool": "bool", "string": "string", "int": "int", "double": "double"}.get(self.kind, "dyn")


class TypeSyntaxError(ValueError):
    pass


def parse_type(text: str) -> ValueType:
    tokens = [m.group(1) or m.group(2) for m in _TOKEN.finditer(text or "") if (m.group(1) or m.group(2) or "").strip()]
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take(expected: str | None = None) -> str:
        nonlocal pos
        token = peek()
        if token is None or (expected is not None and token != expected):
            raise TypeSyntaxError(f"se esperaba {expected or 'un tipo'!r} en {text!r}")
        pos += 1
        return token

    def parse() -> ValueType:
        token = take()
        if token == "{":
            fields: list[tuple[str, ValueType]] = []
            while True:
                name = take()
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    raise TypeSyntaxError(f"nombre de campo inválido {name!r} en {text!r}")
                take(":")
                fields.append((name, parse()))
                if peek() == ",":
                    take(",")
                    continue
                take("}")
                break
            if len({n for n, _ in fields}) != len(fields):
                raise TypeSyntaxError(f"campo repetido en {text!r}")
            base = ValueType("record", fields=tuple(fields))
        elif token in ("list", "tuple"):
            take("<")
            item = parse()
            take(">")
            base = ValueType(token, item=item)
        elif token in _PRIMITIVES:
            base = ValueType(token)
        else:
            raise TypeSyntaxError(f"tipo desconocido {token!r} en {text!r} (hay: {', '.join(_PRIMITIVES)}, list<…>, tuple<…>, {{…}})")
        if peek() == "?":
            take("?")
            return ValueType(base.kind, True, base.item, base.fields)
        return base

    result = parse()
    if peek() is not None:
        raise TypeSyntaxError(f"sobra {peek()!r} en {text!r}")
    return result


def conforms(value: Any, t: ValueType) -> bool:
    if value is None:
        return t.nullable or t.kind == "any"
    if t.kind == "any":
        return True
    if t.kind == "bool":
        return isinstance(value, bool)
    if t.kind == "string":
        return isinstance(value, str)
    if t.kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if t.kind == "double":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t.kind in ("list", "tuple"):
        return isinstance(value, (list, tuple)) and all(conforms(v, t.item) for v in value)  # type: ignore[arg-type]
    if t.kind == "record":
        return isinstance(value, dict) and set(value) == {n for n, _ in t.fields} and all(
            conforms(value[n], ft) for n, ft in t.fields
        )
    return False


def convert(value: Any, t: ValueType) -> Any:
    """El valor en la forma de Python del tipo (tuplas, dicts, float). Lanza
    `TypeError` si no es del tipo."""
    if not conforms(value, t):
        raise TypeError(f"{value!r} no es {t}")
    if value is None or t.kind == "any":
        return value
    if t.kind == "double":
        return float(value)
    if t.kind == "list":
        return [convert(v, t.item) for v in value]  # type: ignore[arg-type]
    if t.kind == "tuple":
        return tuple(convert(v, t.item) for v in value)  # type: ignore[arg-type]
    if t.kind == "record":
        return {n: convert(value[n], ft) for n, ft in t.fields}
    return value


def to_cel(value: Any) -> Any:
    """Un valor de Python como lo lee CEL (tuplas → listas)."""
    if isinstance(value, tuple):
        return [to_cel(v) for v in value]
    if isinstance(value, list):
        return [to_cel(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_cel(v) for k, v in value.items()}
    return value
