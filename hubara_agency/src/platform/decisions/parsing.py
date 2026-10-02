"""Lectura de los archivos de un paquete y de sus condiciones (compartido por
el certificador de las capacidades y el del turno).

  _parse        YAML → modelo Pydantic estricto; cada error es un DB001 con su ruta
  _references   (variable, llave) que lee una condición CEL: `p['x']`, `'x' in p`, `inp.campo`
  _sample       un valor de ejemplo de un tipo declarado
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from src.platform.decisions.engine import Diagnostic
from src.platform.decisions.types import parse_type

_READABLE = "p|choice|conf|th|inp|vars|consts|opt|dom"
#: Lo único que lee la condición de una pregunta (todavía no hay respuestas).
_CONDITION_READS = ("inp", "consts")
_INDEX = re.compile(rf"(?<![\w.])({_READABLE})\s*\[")
_LITERAL = re.compile(r"\s*(['\"])(.*?)\1\s*\]")
_IN = re.compile(rf"(['\"])([^'\"]*)\1\s+in\s+({_READABLE})\b")
_FIELD = re.compile(rf"(?<![\w.])({_READABLE})\.([A-Za-z_][A-Za-z0-9_]*)\b(?!\s*\()")
_ITEM_FIELD = re.compile(r"(?<![\w.])item\.([A-Za-z_][A-Za-z0-9_]*)\b(?!\s*\()")


def _read_yaml(path: Path) -> tuple[Any, str | None]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")), None
    except (OSError, yaml.YAMLError) as exc:
        return None, str(exc).splitlines()[0]


#: Los nombres de las ramas de una unión en la ruta de un error de Pydantic:
#: no son parte de la ruta que escribió quien hizo el paquete.
_UNION_TAGS = frozenset({
    "WhenRow", "OtherwiseRow", "float", "ChoiceAnswer", "str", "int", "bool", "NoneType",
    "BurstQuestion", "EachTopicEntry", "EachMessageEntry",
})


def _loc(loc: tuple[Any, ...]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part not in _UNION_TAGS:
            out += f".{part}" if out else str(part)
    return out


def _parse(model: type[BaseModel], path: Path, label: str, out: list[Diagnostic]) -> Any:
    data, error = _read_yaml(path)
    if error is not None:
        out.append(Diagnostic("DB001", label, f"YAML ilegible: {error}"))
        return None
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        seen: set[str] = set()
        for err in exc.errors():
            where = f"{label}: {_loc(err['loc'])}" if err["loc"] else label
            if where in seen:
                continue  # una unión fallida reporta cada rama: basta una vez por lugar
            seen.add(where)
            out.append(Diagnostic("DB001", where, err["msg"]))
        return None


def _references(source: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(variable, llave) de cada lectura de p/choice/conf/th, y las lecturas
    con llave no literal."""
    refs: list[tuple[str, str]] = []
    bad: list[str] = []
    for match in _INDEX.finditer(source):
        literal = _LITERAL.match(source, match.end())
        if literal is None:
            bad.append(match.group(1))
        else:
            refs.append((match.group(1), literal.group(2)))
    refs += [(m.group(3), m.group(2)) for m in _IN.finditer(source)]
    refs += [(m.group(1), m.group(2)) for m in _FIELD.finditer(source)]
    return refs, bad


def _sample(text: str) -> Any:
    """Un valor de ejemplo del tipo declarado (para ver qué da una condición
    que lee campos `dyn`)."""
    t = parse_type(text).alternatives[0]
    if t.kind == "record":
        return {name: _sample(str(field)) for name, field in t.fields}
    return {"bool": False, "string": "", "int": 0, "double": 0.0, "list": [], "tuple": [], "fixed": []}.get(t.kind)
