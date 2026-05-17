"""R-JSON — Boundary serializability.

Every value crossing `workflow.execute_activity` / `client.start_workflow` must be
a flat, frozen, JSON-serializable `@dataclass`. The contracts.py modules are the
single source of truth for boundary DTOs.

Rules enforced:
  #3 — Every class in a contracts.py module is `@dataclass(frozen=True)`.
       No methods (beyond the dataclass-generated ones). No Pydantic BaseModel.
  #4 — `contracts.py` imports are pure: only `dataclasses`, `typing`, `enum`,
       `__future__`, and other contracts modules.

Exempted classes (pre-existing debt, documented in conftest.R_JSON_FROZEN_EXEMPTIONS):
  - src/platform/contracts.py:TransferDecision
  - src/platform/contracts.py:ScheduleRemarketingDecision
  - src/plugins/chats/agent/remarketing/contracts.py:RemarketingSessionInput
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture.conftest import (
    CONTRACTS_GLOBS,
    R_JSON_FROZEN_EXEMPTIONS,
    iter_agent_files,
    parse_file,
    relative_to_hubara,
)


# Tipos prohibidos como anotación de campo en contracts.py (no JSON-serializables).
_NON_JSON_TYPES: frozenset[str] = frozenset(
    {
        "Path",            # pathlib.Path — use str
        "datetime",        # use ISO string or epoch int
        "date",            # use ISO string
        "time",            # use ISO string
        "timedelta",       # use seconds:int
        "Decimal",         # use float or str
        "UUID",            # use str
        "BaseModel",       # Pydantic — banned outright (R-JSON)
    }
)

# Permitidos como imports en contracts.py.
_ALLOWED_TOP_LEVEL_IMPORTS: frozenset[str] = frozenset(
    {
        "dataclasses",
        "typing",
        "enum",
        "__future__",
    }
)


def _contracts_files() -> list[Path]:
    files: list[Path] = []
    for glob in CONTRACTS_GLOBS:
        files.extend(iter_agent_files(glob))
    return sorted(set(files))


def _has_frozen_dataclass_decorator(node: ast.ClassDef) -> bool:
    """True si la clase tiene `@dataclass(frozen=True)`."""
    for dec in node.decorator_list:
        # @dataclass(frozen=True)
        if isinstance(dec, ast.Call) and _decorator_name(dec.func) == "dataclass":
            for kw in dec.keywords:
                if kw.arg == "frozen" and _is_true_literal(kw.value):
                    return True
        # @dataclass — sin frozen=True
        elif _decorator_name(dec) == "dataclass":
            return False
    return False


def _decorator_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_true_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_dataclass_class(node: ast.ClassDef) -> bool:
    """Detecta `@dataclass` o `@dataclass(...)` con o sin frozen."""
    for dec in node.decorator_list:
        name = _decorator_name(dec.func if isinstance(dec, ast.Call) else dec)
        if name == "dataclass":
            return True
    return False


def _is_pydantic_class(node: ast.ClassDef) -> bool:
    """True si la clase hereda de BaseModel (Pydantic)."""
    for base in node.bases:
        if _decorator_name(base) == "BaseModel":
            return True
    return False


def _has_methods(node: ast.ClassDef) -> list[str]:
    """Devuelve los nombres de métodos definidos en la clase (excluye `__post_init__`)."""
    allowed_dunders = {"__post_init__"}
    method_names: list[str] = []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if item.name not in allowed_dunders:
                method_names.append(item.name)
    return method_names


def _field_type_names(annotation: ast.AST | None) -> list[str]:
    """Extrae los nombres tipo `Path`, `datetime`, etc. de una anotación."""
    if annotation is None:
        return []
    names: list[str] = []
    for sub in ast.walk(annotation):
        if isinstance(sub, ast.Name):
            names.append(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.append(sub.attr)
    return names


# ----------------------------------------------------------------------------
# Test #3 — toda clase en contracts.py es @dataclass(frozen=True), sin métodos,
#           sin BaseModel, sin tipos no serializables.
# ----------------------------------------------------------------------------

def test_contracts_are_frozen_dataclasses() -> None:
    violations: list[str] = []
    for path in _contracts_files():
        rel = relative_to_hubara(path)
        tree = parse_file(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            class_key = f"{rel}:{node.name}"
            if _is_pydantic_class(node):
                violations.append(
                    f"{class_key} inherits from BaseModel — Pydantic is banned at the workflow boundary"
                )
                continue
            if not _is_dataclass_class(node):
                violations.append(
                    f"{class_key} is not a @dataclass — every contracts.py class must be one"
                )
                continue
            if not _has_frozen_dataclass_decorator(node):
                if class_key not in R_JSON_FROZEN_EXEMPTIONS:
                    violations.append(
                        f"{class_key} is @dataclass but not frozen=True"
                    )
            methods = _has_methods(node)
            if methods:
                violations.append(
                    f"{class_key} defines methods {methods} — contracts must be data-only"
                )
            for item in node.body:
                if isinstance(item, ast.AnnAssign):
                    for type_name in _field_type_names(item.annotation):
                        if type_name in _NON_JSON_TYPES:
                            violations.append(
                                f"{class_key}.{getattr(item.target, 'id', '<unknown>')} "
                                f"is typed `{type_name}` — use str/int/float/list instead"
                            )
    assert not violations, (
        "R-JSON violations — contracts.py classes must be frozen dataclasses:\n  "
        + "\n  ".join(violations)
    )


# ----------------------------------------------------------------------------
# Test #4 — contracts.py imports are pure (no I/O, no temporal, no pydantic).
# ----------------------------------------------------------------------------

def test_contracts_imports_are_pure() -> None:
    """contracts.py debe importar SÓLO de dataclasses, typing, enum, __future__,
    o de otros módulos contracts (cross-agent DTOs reusados).
    """
    violations: list[str] = []
    for path in _contracts_files():
        rel = relative_to_hubara(path)
        tree = parse_file(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in _ALLOWED_TOP_LEVEL_IMPORTS:
                        violations.append(
                            f"{rel}:{node.lineno} imports `{alias.name}` — "
                            f"contracts.py must stay pure (R-JSON, R-DIP)"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0 or node.module is None:
                    continue
                top = node.module.split(".")[0]
                if top in _ALLOWED_TOP_LEVEL_IMPORTS:
                    continue
                # Permitimos contracts cruzados (e.g. agent importa platform.contracts).
                if node.module.endswith(".contracts") or node.module == "src.platform.contracts":
                    continue
                violations.append(
                    f"{rel}:{node.lineno} imports `{node.module}` — "
                    f"contracts.py must stay pure (R-JSON, R-DIP)"
                )
    assert not violations, (
        "R-JSON violations — contracts.py has impure imports:\n  "
        + "\n  ".join(violations)
    )
