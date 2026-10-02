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
    default_rule,
)
from src.platform.decisions.expressions import CelExpressions, ExpressionError, ExpressionPort
from src.platform.decisions.model import DOUBT_WORD, Bundle, Capability, Catalog, OtherwiseRow, WhenRow

#: El contrato de paquetes que este motor sabe correr.
ENGINE_CONTRACT = 1

_SLOTS = ("rule", "state", "floor", "same")
_ANSWER_VARS = ("p", "choice", "conf")
_INDEX = re.compile(r"\b(p|choice|conf|th)\s*\[")
_LITERAL = re.compile(r"\s*(['\"])(.*?)\1\s*\]")
_IN = re.compile(r"(['\"])([^'\"]*)\1\s+in\s+(p|choice|conf|th)\b")
_FIELD = re.compile(r"\b(p|choice|conf|th)\.([A-Za-z_][A-Za-z0-9_]*)\b(?!\s*\()")


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


def _value_ok(spec: Capability, value: Any) -> bool:
    if value == DOUBT_WORD:
        return True
    if spec.value == "bool":
        return isinstance(value, bool)
    return isinstance(value, str) and (spec.options is None or value in spec.options)


def _result(value: Any) -> Any:
    return DOUBT if value == DOUBT_WORD else value


class _CapabilityCheck:
    def __init__(self, spec: Capability, label: str, catalog: Catalog, expressions: ExpressionPort) -> None:
        self.spec = spec
        self.label = label
        self.catalog = catalog
        self.expressions = expressions
        self.out: list[Diagnostic] = []

    def add(self, code: str, where: str, message: str) -> None:
        self.out.append(Diagnostic(code, f"{self.label}: {where}", message))

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
            if builtin.value is not None and builtin.value != spec.value:
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

    def rows(self) -> tuple[CompiledRow, ...] | None:
        spec = self.spec
        kinds = {q.id: q.kind for q in spec.questions}
        variables = {
            "p": "map<string,double>", "choice": "map<string,string>", "conf": "map<string,double>",
            "th": "map<string,double>", "rule": "bool" if spec.value == "bool" else "string",
        }
        rows: list[CompiledRow] = []
        failed = False
        last = len(spec.decide) - 1
        for i, row in enumerate(spec.decide):
            if isinstance(row, OtherwiseRow):
                if i != last:
                    self.add("DB008", f"decide[{i}]", "`otherwise` va al final: las filas después nunca se leen")
                    failed = True
                if not _value_ok(spec, row.otherwise):
                    self.add("DB007", f"decide[{i}].otherwise", self._value_message(row.otherwise))
                    failed = True
                rows.append(CompiledRow(None, _result(row.otherwise)))
                continue
            assert isinstance(row, WhenRow)
            where = f"decide[{i}].when"
            if not self._keys_ok(row.when, kinds, where):
                failed = True
                continue
            try:
                compiled = self.expressions.compile(row.when, variables)
            except ExpressionError as exc:
                self.add("DB006", where, f"no compila: {exc}")
                failed = True
                continue
            if not compiled.returns_bool:
                self.add("DB006", where, "la condición tiene que dar true o false")
                failed = True
            if not _value_ok(spec, row.then):
                self.add("DB007", f"decide[{i}].then", self._value_message(row.then))
                failed = True
            rows.append(CompiledRow(compiled, _result(row.then)))
        if not isinstance(spec.decide[-1], OtherwiseRow):
            self.add("DB008", "decide", "falta la fila final `otherwise` (qué pasa si ninguna condición se cumple)")
            failed = True
        return None if failed else tuple(rows)

    def _keys_ok(self, source: str, kinds: dict[str, str], where: str) -> bool:
        ok = True
        refs, bad = _references(source)
        for var in bad:
            self.add("DB005", where, f"{var}[…] lleva una llave literal entre comillas, p. ej. {var}['…']")
            ok = False
        for var, key in refs:
            if var == "th":
                if key not in self.spec.thresholds:
                    self.add("DB005", where, f"umbral no declarado {key!r} (hay: {', '.join(self.spec.thresholds) or 'ninguno'})")
                    ok = False
                continue
            if key not in kinds:
                self.add("DB005", where, f"pregunta no declarada {key!r} (hay: {', '.join(kinds)})")
                ok = False
            elif (var == "p") != (kinds[key] == "noul"):
                want = "p" if kinds[key] == "noul" else "choice / conf"
                self.add("DB011", where, f"{key!r} es una pregunta {kinds[key]}: se lee con {want}")
                ok = False
        return ok

    def _value_message(self, value: Any) -> str:
        if self.spec.value == "bool":
            return f"{value!r} no es true, false ni doubt"
        allowed = ", ".join(self.spec.options or []) or "cualquier texto"
        return f"{value!r} no es una opción de la capacidad ({allowed}) ni doubt"

    def examples(self, compiled: CompiledCapability) -> None:
        declared = {q.id for q in self.spec.questions}
        for i, example in enumerate(self.spec.examples):
            unknown = sorted(set(example.answers) - declared)
            if unknown:
                self.add("DB010", f"examples[{i}].answers", f"preguntas no declaradas {unknown}")
                continue
            if not _value_ok(self.spec, example.expect):
                self.add("DB010", f"examples[{i}].expect", self._value_message(example.expect))
                continue
            got = compiled.decide(answers=example.answers, rule=example.rule)
            want = _result(example.expect)
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
        rows = check.rows()
        if rows is not None and not check.out:
            capability = CompiledCapability(spec, rows, bundle=f"{bundle.id}@{bundle.version}")
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


__all__ = ["ENGINE_CONTRACT", "check_bundle", "default_rule", "load_bundle"]
