"""R-STATELESS — Activity hygiene.

Activities must rebuild every dependency they need on each invocation by calling
a factory from `composition.py`. This rules out module-level mutable state
inside `activities/*.py` files: `_REGISTRY = []`, `_CACHE: dict = {}`,
`_CLIENT = None`, etc.

Convention recap:
  - `composition.py` and `worker.py` ARE allowed to hold singletons (they're
    composition roots, not activity code). They cache via `@lru_cache(maxsize=1)`
    or a module-level guard variable.
  - `activities/*.py` may declare UPPER_SNAKE constants (immutable, no side effect
    on import). Anything else with a literal `dict`/`list`/`set` initializer is a
    smell that drifts into hidden state.

Test:
  #5 — `test_activities_have_no_module_level_mutable_state`
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture.conftest import (
    AGENT_ACTIVITIES_GLOB,
    iter_agent_files,
    parse_file,
    relative_to_hubara,
)

# Container literals at module scope are the canonical smell ("the registry that
# grows by side effect"). Allow `None`, strings, numbers, tuples, frozensets —
# they are immutable so they cannot drift.
_MUTABLE_LITERAL_NODES: tuple[type, ...] = (ast.Dict, ast.List, ast.Set)


def _activity_files() -> list[Path]:
    """Cada `activities/<concept>.py` que no sea `__init__.py`."""
    return [p for p in iter_agent_files(AGENT_ACTIVITIES_GLOB) if p.name != "__init__.py"]


def _platform_activity_files() -> list[Path]:
    """Las activities cross-agent bajo platform/temporal/."""
    src_root = Path(__file__).resolve().parents[2] / "src"
    candidates = [
        src_root / "platform" / "temporal" / "activities.py",
        src_root / "platform" / "temporal" / "dispatcher.py",
    ]
    return [p for p in candidates if p.is_file()]


def _is_upper_snake(name: str) -> bool:
    return name.isupper() and all(c.isalnum() or c == "_" for c in name)


def _is_mutable_assignment(node: ast.AST) -> tuple[str, str] | None:
    """Si `node` es `name = <mutable literal>` a nivel módulo, devuelve (name, type).

    Excluye constantes UPPER_SNAKE (convención: inmutables semánticamente).
    """
    if isinstance(node, ast.Assign):
        targets = node.targets
        value = node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets = [node.target]
        value = node.value
    else:
        return None

    if not isinstance(value, _MUTABLE_LITERAL_NODES):
        return None

    for target in targets:
        if isinstance(target, ast.Name) and not _is_upper_snake(target.id):
            return (target.id, type(value).__name__)
    return None


def test_activities_have_no_module_level_mutable_state() -> None:
    """`activities/*.py` no puede tener literales mutables a nivel módulo.

    Las activities deben pedir sus deps a `composition.py` (factories) en cada
    invocación. Estado a nivel módulo ⇒ rompe R-STATELESS porque persiste entre
    invocaciones de la activity dentro del mismo worker.
    """
    files = _activity_files() + _platform_activity_files()
    violations: list[str] = []

    for path in files:
        rel = relative_to_hubara(path)
        tree = parse_file(path)
        for node in tree.body:  # solo top-level (module body)
            found = _is_mutable_assignment(node)
            if found is None:
                continue
            name, kind = found
            violations.append(
                f"{rel}:{node.lineno} module-level `{name}: {kind}` — "
                f"activities must build deps via composition factories on each call"
            )

    assert not violations, (
        "R-STATELESS violations — module-level mutable state in activities:\n  "
        + "\n  ".join(violations)
    )
