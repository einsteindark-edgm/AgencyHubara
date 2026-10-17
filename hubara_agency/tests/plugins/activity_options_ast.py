"""Qué opciones lleva cada activity que agenda un workflow (AST, sin importar
nada): base de los gates de reintentos finitos y de heartbeat (auditoría de
Temporal, 2026-10-15; premortem 2026-10-16).

Un `workflow.execute_activity(act, **PRESET)` se resuelve siguiendo el preset:
un dict literal del módulo (`_FAST = {...}`, `{**_OTRO, "k": v}`), un alias
(`LLM_ACTIVITY_OPTIONS = _LLM_OPTIONS`) o un import (`from x import _FAST`, con
o sin `as`, también dentro de `with workflow.unsafe.imports_passed_through()`).
Lo que no se puede resolver queda como `None`: el gate lo rechaza, para que un
preset que nadie mira no esconda una activity sin política.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
_SCHEDULERS = {"execute_activity", "start_activity", "execute_local_activity", "start_local_activity"}


@dataclass(frozen=True)
class ScheduledActivity:
    where: str  # "src/...py:LINEA"
    target: str | None  # nombre de la función o de la activity (string)
    options: frozenset[str] | None  # None = un preset que no se pudo resolver


def _module_path(module: str) -> Path | None:
    if not module.startswith("src."):
        return None
    base = SRC.parent / Path(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _top_level(tree: ast.Module) -> list[ast.stmt]:
    """El cuerpo del módulo, entrando a los `with` e `if` de nivel módulo."""
    out: list[ast.stmt] = []
    pending = list(tree.body)
    while pending:
        node = pending.pop(0)
        out.append(node)
        if isinstance(node, (ast.With, ast.AsyncWith, ast.If, ast.Try)):
            pending[0:0] = [*node.body, *getattr(node, "orelse", []), *getattr(node, "finalbody", [])]
    return out


@lru_cache(maxsize=None)
def _parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _dict_keys(node: ast.AST, path: Path, seen: frozenset[tuple[str, str]]) -> frozenset[str] | None:
    if isinstance(node, ast.Dict):
        keys: set[str] = set()
        for key, value in zip(node.keys, node.values):
            if key is None:  # `**OTRO`
                inner = _name_keys(value, path, seen)
                if inner is None:
                    return None
                keys |= inner
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value)
            else:
                return None
        return frozenset(keys)
    if isinstance(node, ast.Name):
        return preset_keys(node.id, path, seen)
    return None


def _name_keys(node: ast.AST, path: Path, seen: frozenset[tuple[str, str]]) -> frozenset[str] | None:
    return preset_keys(node.id, path, seen) if isinstance(node, ast.Name) else None


def preset_keys(name: str, path: Path, seen: frozenset[tuple[str, str]] = frozenset()) -> frozenset[str] | None:
    """Las claves del preset `name` visto desde el módulo `path`, o None."""
    key = (str(path), name)
    if key in seen:
        return None
    seen = seen | {key}
    for node in _top_level(_parsed(path)):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return _dict_keys(node.value, path, seen)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name and node.value:
            return _dict_keys(node.value, path, seen)
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    source = _module_path(node.module)
                    return preset_keys(alias.name, source, seen) if source else None
    return None


def scheduled_activities(path: Path) -> list[ScheduledActivity]:
    """Cada `workflow.execute_activity`/`start_activity` (y las locales) del módulo."""
    out: list[ScheduledActivity] = []
    for node in ast.walk(_parsed(path)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SCHEDULERS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "workflow"
        ):
            continue
        first = node.args[0] if node.args else None
        target = first.id if isinstance(first, ast.Name) else first.value if isinstance(first, ast.Constant) else None
        options: set[str] | None = set()
        for kw in node.keywords:
            if kw.arg is not None:
                options.add(kw.arg)
                continue
            inner = _name_keys(kw.value, path, frozenset())
            if inner is None:
                options = None
                break
            options |= inner
        shown = path.relative_to(SRC.parent) if path.is_relative_to(SRC.parent) else path
        out.append(
            ScheduledActivity(
                where=f"{shown}:{node.lineno}",
                target=target if isinstance(target, str) else None,
                options=frozenset(options) if options is not None else None,
            )
        )
    return out


def all_scheduled_activities() -> list[ScheduledActivity]:
    out: list[ScheduledActivity] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if any(s in text for s in _SCHEDULERS):
            out.extend(scheduled_activities(path))
    return out
