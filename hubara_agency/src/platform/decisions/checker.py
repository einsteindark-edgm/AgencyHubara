"""El certificador de paquetes de decisión: «¿compila?» (PAQUETES_DE_DECISION.md §6).

Un paquete que no pasa no existe: `load_bundle` lo rechaza y el CLI
(`src.sdk.cli decisions check`) sale con error. Cada diagnóstico lleva su
código y la ruta exacta (`capabilities/baja.yaml: decide[0].when`).

  DB001 estructura (llave desconocida, tipo, campo faltante) — Pydantic estricto
  DB002 contrato del motor que no se sabe correr
  DB003 capacidad listada sin archivo, archivo sin listar o nombre distinto
  DB004 builtin inexistente, de otra clase, de otro tipo o con otros parámetros
  DB005 pregunta repetida, o llave de p/choice/conf/th no literal o no declarada
  DB006 condición que no compila o no devuelve bool
  DB007 resultado (`then`/`otherwise`) que no es del tipo de la capacidad
  DB008 tabla sin `otherwise` al final, o filas después de él
  DB009 umbral fuera de [0, 1]
  DB010 ejemplo que no da lo esperado (o que usa una pregunta no declarada)
  DB011 p sobre una pregunta choice, o choice/conf sobre una noul
  DB012 piso obligatorio cambiado (p. ej. la baja legal)
  DB013 el id del paquete no es el nombre de su carpeta (o el paquete no existe)
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from src.platform.decisions.engine import (
    DOUBT,
    BundleError,
    CompiledBundle,
    CompiledCapability,
    CompiledRow,
    Diagnostic,
)
from src.platform.decisions.expressions import CelExpressions, ExpressionError, ExpressionPort
from src.platform.decisions.model import DOUBT_WORD, Bundle, Capability, Catalog, OtherwiseRow, WhenRow
from src.platform.decisions.types import ValueType, conforms, convert, parse_type

#: El contrato de paquetes que este motor sabe correr.
ENGINE_CONTRACT = 1

_SLOTS = ("rule", "state", "floor", "same")
_READABLE = "p|choice|conf|th|inp|vars|consts"
_INDEX = re.compile(rf"(?<![\w.])({_READABLE})\s*\[")
_LITERAL = re.compile(r"\s*(['\"])(.*?)\1\s*\]")
_IN = re.compile(rf"(['\"])([^'\"]*)\1\s+in\s+({_READABLE})\b")
_FIELD = re.compile(rf"(?<![\w.])({_READABLE})\.([A-Za-z_][A-Za-z0-9_]*)\b(?!\s*\()")


def _read_yaml(path: Path) -> tuple[Any, str | None]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")), None
    except (OSError, yaml.YAMLError) as exc:
        return None, str(exc).splitlines()[0]


def _loc(loc: tuple[Any, ...]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part not in ("WhenRow", "OtherwiseRow", "float", "ChoiceAnswer", "str", "int", "bool"):
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


def _value_ok(spec: Capability, value_type: ValueType, value: Any) -> bool:
    if value == DOUBT_WORD:
        return True
    if not conforms(value, value_type):
        return False
    return value_type.kind != "string" or spec.options is None or value in spec.options or value is None


def _result(value: Any, value_type: ValueType) -> Any:
    return DOUBT if value == DOUBT_WORD else convert(value, value_type)


def _is_expr(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"expr"} and isinstance(value["expr"], str)


#: Lo que una expresión de salida puede devolver para cada clase de tipo.
_RETURNS_FOR = {
    "bool": {"bool"}, "string": {"string"}, "int": {"int"}, "double": {"double", "int"},
    "list": {"list"}, "tuple": {"list"}, "record": {"map"}, "any": None,
}


class _CapabilityCheck:
    def __init__(self, spec: Capability, label: str, catalog: Catalog, expressions: ExpressionPort) -> None:
        self.spec = spec
        self.label = label
        self.catalog = catalog
        self.expressions = expressions
        self.type = parse_type(spec.value)
        self.fields = dict(catalog.inputs.get(spec.input, {}))
        self.out: list[Diagnostic] = []

    def add(self, code: str, where: str, message: str) -> None:
        self.out.append(Diagnostic(code, f"{self.label}: {where}", message))

    # ── builtins ──────────────────────────────────────────────────────────

    def builtins(self) -> None:
        spec = self.spec
        if spec.input not in self.catalog.inputs:
            self.add("DB004", "input", f"tipo de entrada desconocido {spec.input!r} (hay {', '.join(self.catalog.inputs)})")
        for slot in _SLOTS:
            ref = getattr(spec, slot)
            if ref is None:
                continue
            builtin = self.catalog.builtins.get(ref.builtin)
            where = f"{slot}.builtin"
            if builtin is None:
                options = sorted(n for n, b in self.catalog.builtins.items() if b.kind == slot)
                self.add("DB004", where, f"builtin desconocido {ref.builtin!r} (de clase {slot} hay: {', '.join(options)})")
                continue
            if builtin.kind != slot:
                self.add("DB004", where, f"{ref.builtin!r} es un builtin de clase {builtin.kind}, no {slot}")
            if builtin.input is not None and builtin.input != spec.input:
                self.add("DB004", where, f"{ref.builtin!r} lee {builtin.input}, la capacidad recibe {spec.input}")
            if builtin.value is not None and str(parse_type(builtin.value)) != str(self.type):
                self.add("DB004", where, f"{ref.builtin!r} es para value {builtin.value}, la capacidad es {spec.value}")
            missing = sorted(set(builtin.params) - set(ref.params))
            extra = sorted(set(ref.params) - set(builtin.params))
            if missing or extra:
                self.add("DB004", f"{slot}.with", f"parámetros de {ref.builtin!r}: faltan {missing}, sobran {extra}")
            for name, kind in builtin.params.items():
                if name in ref.params and not _param_ok(ref.params[name], kind):
                    self.add("DB004", f"{slot}.with.{name}", f"se esperaba {kind}")
        required = self.catalog.required_floors.get(spec.capability)
        if required is not None and spec.floor.builtin != required:
            self.add("DB012", "floor.builtin", f"el piso de {spec.capability} es obligatorio: {required!r}")

    def questions_and_thresholds(self) -> None:
        seen: set[str] = set()
        for i, question in enumerate(self.spec.questions):
            if question.id in seen:
                self.add("DB005", f"questions[{i}].id", f"pregunta repetida {question.id!r}")
            seen.add(question.id)
        for name, value in self.spec.thresholds.items():
            if not 0.0 <= value <= 1.0:
                self.add("DB009", f"thresholds.{name}", f"{value} está fuera de [0, 1]")

    # ── expresiones ───────────────────────────────────────────────────────

    def _variables(self) -> dict[str, str]:
        return {
            "p": "map<string,double>", "choice": "map<string,string>", "conf": "map<string,double>",
            "th": "map<string,double>", "rule": self.type.cel_var,
            "inp": "map<string,dyn>", "vars": "map<string,dyn>", "consts": "map<string,dyn>",
        }

    def _compile(self, source: str, where: str, known_vars: set[str]) -> Any:
        if not self._keys_ok(source, where, known_vars):
            return None
        try:
            return self.expressions.compile(source, self._variables())
        except ExpressionError as exc:
            self.add("DB006", where, f"no compila: {exc}")
            return None

    def vars(self) -> tuple[tuple[str, Any], ...] | None:
        compiled: list[tuple[str, Any]] = []
        known: set[str] = set()
        failed = False
        for name, source in self.spec.vars.items():
            expression = self._compile(source, f"vars.{name}", known)
            if expression is None:
                failed = True
            else:
                compiled.append((name, expression))
            known.add(name)
        return None if failed else tuple(compiled)

    def rows(self) -> tuple[CompiledRow, ...] | None:
        spec = self.spec
        known = set(spec.vars)
        rows: list[CompiledRow] = []
        failed = False
        last = len(spec.decide) - 1
        for i, row in enumerate(spec.decide):
            if isinstance(row, OtherwiseRow):
                if i != last:
                    self.add("DB008", f"decide[{i}]", "`otherwise` va al final: las filas después nunca se leen")
                    failed = True
                result = self._output(row.otherwise, f"decide[{i}].otherwise", known)
                failed = failed or result is None
                rows.append(CompiledRow(None, *(result or (None, None))))
                continue
            assert isinstance(row, WhenRow)
            where = f"decide[{i}].when"
            compiled = self._compile(row.when, where, known)
            if compiled is None:
                failed = True
            elif not compiled.returns_bool:
                self.add("DB006", where, "la condición tiene que dar true o false")
                failed = True
            result = self._output(row.then, f"decide[{i}].then", known)
            failed = failed or result is None
            rows.append(CompiledRow(compiled, *(result or (None, None))))
        if not isinstance(spec.decide[-1], OtherwiseRow):
            self.add("DB008", "decide", "falta la fila final `otherwise` (qué pasa si ninguna condición se cumple)")
            failed = True
        return None if failed else tuple(rows)

    def _output(self, value: Any, where: str, known: set[str]) -> tuple[Any, Any] | None:
        """(valor ya convertido o DOUBT, expresión) de un `then`/`otherwise`."""
        if _is_expr(value):
            expression = self._compile(value["expr"], f"{where}.expr", known)
            if expression is None:
                return None
            allowed = _RETURNS_FOR.get(self.type.kind)
            ok = (
                allowed is None
                or expression.returns in allowed | {"dyn"}
                or (self.type.nullable and expression.returns == "null")
            )
            if not ok:
                self.add("DB007", f"{where}.expr", f"la expresión da {expression.returns} y la capacidad es {self.spec.value}")
                return None
            return None, expression
        if not _value_ok(self.spec, self.type, value):
            self.add("DB007", where, self._value_message(value))
            return None
        return _result(value, self.type), None

    def _keys_ok(self, source: str, where: str, known_vars: set[str]) -> bool:
        ok = True
        kinds = {q.id: q.kind for q in self.spec.questions}
        refs, bad = _references(source)
        for var in bad:
            self.add("DB005", where, f"{var}[…] lleva una llave literal entre comillas, p. ej. {var}['…']")
            ok = False
        for var, key in refs:
            problem = self._key_problem(var, key, kinds, known_vars)
            if problem is not None:
                self.add(problem[0], where, problem[1])
                ok = False
        return ok

    def _key_problem(self, var: str, key: str, kinds: dict[str, str], known_vars: set[str]) -> tuple[str, str] | None:
        if var == "th":
            if key not in self.spec.thresholds:
                return "DB005", f"umbral no declarado {key!r} (hay: {', '.join(self.spec.thresholds) or 'ninguno'})"
            return None
        if var == "inp":
            if key not in self.fields:
                declared = ", ".join(self.fields) or "ninguno"
                return "DB005", f"campo de entrada no declarado {key!r} en {self.spec.input} (hay: {declared})"
            return None
        if var == "vars":
            if key not in known_vars:
                declared = ", ".join(sorted(known_vars)) or "ninguna"
                return "DB005", f"variable no declarada (o declarada después) {key!r} (hay: {declared})"
            return None
        if var == "consts":
            if key not in self.catalog.constants:
                return "DB005", f"constante no declarada {key!r} (hay: {', '.join(self.catalog.constants) or 'ninguna'})"
            return None
        if key not in kinds:
            return "DB005", f"pregunta no declarada {key!r} (hay: {', '.join(kinds)})"
        if (var == "p") != (kinds[key] == "noul"):
            want = "p" if kinds[key] == "noul" else "choice / conf"
            return "DB011", f"{key!r} es una pregunta {kinds[key]}: se lee con {want}"
        return None

    def _value_message(self, value: Any) -> str:
        if self.type.kind == "string" and self.spec.options is not None:
            return f"{value!r} no es una opción de la capacidad ({', '.join(self.spec.options)}) ni doubt"
        return f"{value!r} no es {self.spec.value} ni doubt"

    # ── ejemplos ──────────────────────────────────────────────────────────

    def examples(self, compiled: CompiledCapability) -> None:
        declared = {q.id for q in self.spec.questions}
        for i, example in enumerate(self.spec.examples):
            unknown = sorted(set(example.answers) - declared)
            if unknown:
                self.add("DB010", f"examples[{i}].answers", f"preguntas no declaradas {unknown}")
                continue
            fields = sorted(set(example.input) - set(self.fields))
            if fields:
                self.add("DB010", f"examples[{i}].input", f"campos de entrada no declarados {fields} en {self.spec.input}")
                continue
            if example.rule is not None and not conforms(example.rule, self.type):
                self.add("DB010", f"examples[{i}].rule", f"{example.rule!r} no es {self.spec.value}")
                continue
            if not _value_ok(self.spec, self.type, example.expect):
                self.add("DB010", f"examples[{i}].expect", self._value_message(example.expect))
                continue
            got = compiled.decide(answers=example.answers, rule=example.rule, inp=example.input)
            want = _result(example.expect, self.type)
            if got is not want and got != want:
                shown = DOUBT_WORD if got is DOUBT else got
                self.add("DB010", f"examples[{i}]", f"se esperaba {example.expect!r} y la tabla da {shown!r}")


def _param_ok(value: Any, kind: str) -> bool:
    checks: dict[str, Callable[[Any], bool]] = {
        "str": lambda v: isinstance(v, str),
        "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "bool": lambda v: isinstance(v, bool),
    }
    return checks[kind](value)


def _build(bundle_dir: Path, catalog_path: Path, expressions: ExpressionPort | None) -> tuple[CompiledBundle | None, list[Diagnostic]]:
    expressions = expressions or CelExpressions()
    bundle_dir, catalog_path = Path(bundle_dir), Path(catalog_path)
    out: list[Diagnostic] = []
    catalog = _parse(Catalog, catalog_path, catalog_path.name, out)
    bundle = _parse(Bundle, bundle_dir / "bundle.yaml", "bundle.yaml", out)
    if catalog is None or bundle is None:
        return None, out
    if bundle.id != bundle_dir.name:
        out.append(Diagnostic("DB013", "bundle.yaml: id", f"el paquete se llama {bundle.id!r} pero su carpeta es {bundle_dir.name!r}"))
    for label, contract in (("bundle.yaml", bundle.engine_contract), (catalog_path.name, catalog.engine_contract)):
        if contract != ENGINE_CONTRACT:
            out.append(Diagnostic("DB002", f"{label}: engine_contract", f"este motor corre el contrato {ENGINE_CONTRACT}, no {contract}"))
    if out:
        return None, out
    files = {p.stem: p for p in sorted((bundle_dir / "capabilities").glob("*.yaml"))}
    for name in bundle.capabilities:
        if name not in files:
            out.append(Diagnostic("DB003", "bundle.yaml: capabilities", f"{name!r} no tiene archivo capabilities/{name}.yaml"))
    for stem in files:
        if stem not in bundle.capabilities:
            out.append(Diagnostic("DB003", f"capabilities/{stem}.yaml", "el archivo no está en bundle.yaml: capabilities"))
    compiled: dict[str, CompiledCapability] = {}
    for name in bundle.capabilities:
        if name not in files:
            continue
        label = f"capabilities/{name}.yaml"
        spec = _parse(Capability, files[name], label, out)
        if spec is None:
            continue
        if spec.capability != name:
            out.append(Diagnostic("DB003", f"{label}: capability", f"se llama {spec.capability!r} pero el archivo es {name}.yaml"))
            continue
        check = _CapabilityCheck(spec, label, catalog, expressions)
        check.builtins()
        check.questions_and_thresholds()
        variables = check.vars()
        rows = check.rows()
        if rows is not None and variables is not None and not check.out:
            capability = CompiledCapability(
                spec, rows, bundle=f"{bundle.id}@{bundle.version}", value_type=check.type, vars=variables,
                constants={name: c.value for name, c in catalog.constants.items()},
                input_fields=tuple(check.fields),
            )
            check.examples(capability)
            if not check.out:
                compiled[name] = capability
        out += check.out
    if out:
        return None, out
    return CompiledBundle(bundle.id, bundle.version, bundle.oracle, compiled), []


def check_bundle(bundle_dir: Path, catalog_path: Path, *, expressions: ExpressionPort | None = None) -> list[Diagnostic]:
    """Los diagnósticos del paquete (vacío = certificado)."""
    return _build(bundle_dir, catalog_path, expressions)[1]


def load_bundle(bundle_dir: Path, catalog_path: Path, *, expressions: ExpressionPort | None = None) -> CompiledBundle:
    """El paquete certificado y compilado; `BundleError` si no pasa."""
    bundle, diagnostics = _build(bundle_dir, catalog_path, expressions)
    if bundle is None:
        raise BundleError(diagnostics)
    return bundle


__all__ = ["ENGINE_CONTRACT", "check_bundle", "load_bundle"]
