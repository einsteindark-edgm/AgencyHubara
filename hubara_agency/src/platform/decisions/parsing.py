"""Lectura de los archivos de un paquete y de sus condiciones (compartido por
el certificador de las capacidades y el del turno).

  _parse        YAML → modelo Pydantic estricto; cada error es un DB001 con su ruta
  _references   (variable, llave) que lee una condición CEL: `p['x']`, `'x' in p`, `inp.campo`
  unkeyed_reads las lecturas sin llave a la vista (`(choice)['x']`, `size(p)`): no se validan
  domain_problems  `dom.seccion.campo` contra lo que declara el catálogo
  item_field_problems  `i.campo` dentro de `items.filter(i, …)` contra los campos del ítem
  _sample       un valor de ejemplo de un tipo declarado
  literal_comparisons  los literales con que se compara una lectura (`choice['x'] == 'a'`)
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
_STRINGS = re.compile(r"'(?:[^'\\]|\\.)*'" r'|"(?:[^"\\]|\\.)*"')
_TOKEN = re.compile(rf"(?<![\w.])({_READABLE})\b")
_DOM_PATH = re.compile(
    r"""(?<![\w.])dom(?:\.([A-Za-z_]\w*)|\[\s*['"]([^'"]+)['"]\s*\])"""
    r"""(?:\.([A-Za-z_]\w*)\b(?!\s*\()|\[\s*['"]([^'"]+)['"]\s*\])?"""
)


class _UniqueKeyLoader(yaml.SafeLoader):
    """`safe_load` que rechaza una llave repetida (YAML se quedaba con la
    última, en silencio: una regla o un umbral duplicado se perdía)."""


def _unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"llave repetida {key!r} (línea {key_node.start_mark.line + 1})", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _read_yaml(path: Path) -> tuple[Any, str | None]:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader), None  # noqa: S506 — SafeLoader
    except (OSError, yaml.YAMLError) as exc:
        return None, " ".join(str(exc).split())[:300]


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


def _masked(source: str) -> str:
    """El texto con el contenido de cada literal en blanco (mismo largo)."""
    return _STRINGS.sub(lambda m: m.group(0)[0] + " " * (len(m.group(0)) - 2) + m.group(0)[-1], source)


def unkeyed_reads(source: str) -> list[str]:
    """Las variables que la condición lee sin una llave literal a la vista:
    `(choice)['x']`, `('x') in choice`, `choice.exists(…)`, `size(p)`. Compilan,
    pero el certificador no ve qué llave leen: una mal escrita nunca se cumple
    en ejecución (premortem 2026-10-02, P-H10)."""
    masked = _masked(source)
    out: list[str] = []
    for match in _TOKEN.finditer(masked):
        after = masked[match.end():]
        if re.match(r"\s*\[", after) or re.match(r"\.[A-Za-z_]\w*\b(?!\s*\()", after):
            continue  # `p[…]` (la llave la valida _references) o `inp.campo`
        if re.search(r"""(['"])[^'"]*\1\s+in\s+$""", masked[: match.start()]):
            continue  # `'x' in p`
        out.append(match.group(1))
    return out


def domain_problems(source: str, domain: dict[str, Any]) -> list[str]:
    """`dom.seccion.campo` (o `dom['seccion']['campo']`) contra el dominio que
    declara el catálogo; el primer nivel lo valida quien lee _references."""
    out: list[str] = []
    for match in _DOM_PATH.finditer(source):
        name = match.group(1) or match.group(2)
        field = match.group(3) or match.group(4)
        declared = domain.get(name)
        if declared is None or field is None:
            continue
        if not isinstance(declared, dict):
            out.append(f"dom.{name} es un campo ({declared}), no una sección: no tiene {field!r}")
        elif field not in declared:
            out.append(f"campo del dominio no declarado {name}.{field} (hay en {name}: {', '.join(declared)})")
    return out


_MACRO = re.compile(r"\.(all|exists_one|exists|filter|map)\(\s*([A-Za-z_]\w*)\s*,")
_CHAIN_TOKEN = re.compile(r"[A-Za-z_]\w*|[(\[]|[)\]]")


def _closing(masked: str, open_at: int) -> int:
    """La posición del paréntesis que cierra el de `open_at` (o el final)."""
    depth = 0
    for i in range(open_at, len(masked)):
        if masked[i] in "([":
            depth += 1
        elif masked[i] in ")]":
            depth -= 1
            if depth == 0:
                return i
    return len(masked)


def _receiver(masked: str, dot: int) -> str:
    """Lo que está antes de `.macro(`: nombres, puntos y paréntesis balanceados."""
    i = dot
    while i > 0:
        c = masked[i - 1]
        if c.isalnum() or c in "_.":
            i -= 1
        elif c in ")]":
            depth, j = 0, i - 1
            while j >= 0:
                if masked[j] in ")]":
                    depth += 1
                elif masked[j] in "([":
                    depth -= 1
                    if depth == 0:
                        break
                j -= 1
            i = max(j, 0)
        else:
            break
    return masked[i:dot]


def is_item_list(expr: str, item_vars: set[str]) -> bool:
    """¿La expresión es la lista de ítems (o un `filter` de ella)? `items`,
    `items.filter(i, …)` o `vars.x` cuando `x` lo es."""
    names: list[str] = []
    depth = 0
    for token in _CHAIN_TOKEN.findall(_masked(expr)):
        if token in "([":
            depth += 1
        elif token in ")]":
            depth -= 1
        elif depth == 0:
            names.append(token)
    if names[:1] == ["items"]:
        rest = names[1:]
    elif names[:1] == ["vars"] and len(names) > 1 and names[1] in item_vars:
        rest = names[2:]
    else:
        return False
    return all(name == "filter" for name in rest)


def item_field_problems(source: str, allowed: set[str], item_vars: set[str]) -> list[str]:
    """`i.campo` dentro de cada macro que recorre los ítems (`items.filter(i,
    …)`, `vars.x.exists(j, …)`): un campo que el ítem no trae hace `has(i.x)`
    false para siempre y `i.x < …` un error en ejecución (premortem
    2026-10-02, P-H10)."""
    masked = _masked(source)
    out: list[str] = []
    for macro in _MACRO.finditer(masked):
        if not is_item_list(_receiver(masked, macro.start()), item_vars):
            continue
        var = macro.group(2)
        body = masked[macro.end(1): _closing(masked, macro.end(1))]
        for read in re.finditer(rf"(?<![\w.]){re.escape(var)}\.([A-Za-z_]\w*)\b(?!\s*\()", body):
            problem = f"{var}.{read.group(1)}: campo que el ítem no trae (hay: {', '.join(sorted(allowed))})"
            if read.group(1) not in allowed and problem not in out:
                out.append(problem)
    return out


def _sample(text: str) -> Any:
    """Un valor de ejemplo del tipo declarado (para ver qué da una condición
    que lee campos `dyn`)."""
    t = parse_type(text).alternatives[0]
    if t.kind == "record":
        return {name: _sample(str(field)) for name, field in t.fields}
    return {"bool": False, "string": "", "int": 0, "double": 0.0, "list": [], "tuple": [], "fixed": []}.get(t.kind)


_STR = r"""(?:'([^'\\]*)'|"([^"\\]*)")"""


def literal_comparisons(source: str, target: str) -> list[tuple[str, str]]:
    """(llave, literal) de cada comparación de `target` con un texto literal:
    `target == 'a'`, `target != 'a'`, `'a' == target` o `target in ['a', 'b']`.
    `target` es una regex; si trae el grupo `key`, es la llave leída
    (`choice['q']`), si no, "". Sirve para ver que el literal existe (una
    opción mal escrita compila y en ejecución nunca se cumple)."""
    out: list[tuple[str, str]] = []

    def key_of(match: re.Match[str]) -> str:
        return match.groupdict().get("key") or ""

    for m in re.finditer(rf"(?:{target})\s*(?:==|!=)\s*{_STR}", source):
        out.append((key_of(m), m.group(m.re.groups - 1) or m.group(m.re.groups) or ""))
    for m in re.finditer(rf"{_STR}\s*(?:==|!=)\s*(?:{target})", source):
        out.append((key_of(m), m.group(1) or m.group(2) or ""))
    for m in re.finditer(rf"(?:{target})\s+in\s+\[([^\]]*)\]", source):
        out += [(key_of(m), a or b) for a, b in re.findall(_STR, m.group(m.re.groups))]
    return out


#: `choice['q']` (con la llave en el grupo `key`).
CHOICE_READ = r"""choice\[\s*['"](?P<key>[^'"]+)['"]\s*\]"""
