"""R-JSON footgun F1 — nested dataclasses + PEP 563 in workflow boundaries.

Post-mortem (workflow run df5a8fe2-bb7c-4627-b861-dc19643467be):

  ``orchestration.dispatch_event`` returned ``DispatchResult`` whose field
  ``matches: list[DispatchedTransition]`` triggered Temporal's default
  ``DataConverter`` to call ``get_type_hints(DispatchResult)`` in the calling
  workflow's sandbox. With ``from __future__ import annotations`` the hint
  was the string ``"list[DispatchedTransition]"`` and the sandbox's
  restricted namespace could not resolve ``DispatchedTransition`` — crashing
  with ``NameError`` and freezing the workflow in an infinite retry loop.

Rule enforced here:
  A module that defines a ``@activity.defn`` whose return type annotation
  is a dataclass containing a NESTED dataclass field (``list[Foo]``,
  ``dict[str, Foo]``, ``tuple[Foo, ...]``, ``Optional[Foo]`` where ``Foo``
  is another dataclass in the same module) MUST NOT use
  ``from __future__ import annotations``. Without future annotations, the
  hints are evaluated at class-definition time and stored as real
  ``types.GenericAlias`` objects — ``get_type_hints`` then returns them
  directly without any ``eval`` in the workflow sandbox.

How to fix a violation:
  1. Remove ``from __future__ import annotations`` from the offending file.
  2. Ensure nested dataclass classes are defined BEFORE the dataclass that
     references them (eager evaluation order matters).
  3. As a fallback if removal is impractical, change the return type to
     a flat dict / primitive collection (lose nested typing).

See: ADR-2026-05-20-declarative-orchestration.md + this premortem footgun F1.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture.conftest import iter_agent_files, parse_file, relative_to_hubara


# ── Modules where this rule applies ────────────────────────────────────────
# Add new entries when activities with nested-dataclass returns cross workflow
# boundaries. The rule is local to these files; the rest of the codebase is
# free to use `from __future__ import annotations`.
_TARGET_FILES: tuple[str, ...] = (
    "hubara_agency/src/platform/orchestration/dispatcher.py",
)


def _has_future_annotations(tree: ast.Module) -> bool:
    """True si el módulo tiene ``from __future__ import annotations``."""
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            for alias in node.names:
                if alias.name == "annotations":
                    return True
    return False


def _has_activity_defn(tree: ast.Module) -> bool:
    """True si el módulo define al menos una ``@activity.defn``."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                # @activity.defn  /  @activity.defn(name="...")
                target = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(target, ast.Attribute) and target.attr == "defn":
                    if (
                        isinstance(target.value, ast.Name)
                        and target.value.id == "activity"
                    ):
                        return True
    return False


def _dataclass_names_in_module(tree: ast.Module) -> set[str]:
    """Devuelve el set de nombres de clases con ``@dataclass(...)`` en el módulo."""
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Name) and target.id == "dataclass":
                names.add(node.name)
                break
    return names


def _annotation_references(annotation: ast.AST) -> set[str]:
    """Extrae todos los nombres tipo en una anotación (incluyendo dentro de generics)."""
    refs: set[str] = set()
    for sub in ast.walk(annotation):
        if isinstance(sub, ast.Name):
            refs.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            refs.add(sub.attr)
    return refs


def _dataclass_with_nested_dataclass_field(
    tree: ast.Module,
    dataclass_names: set[str],
) -> list[str]:
    """Devuelve nombres de clases que tienen al menos un campo con otra dataclass anidada."""
    offenders: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name not in dataclass_names:
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and stmt.annotation is not None:
                refs = _annotation_references(stmt.annotation)
                if refs & dataclass_names - {node.name}:
                    offenders.append(node.name)
                    break
    return offenders


def test_no_future_annotations_with_nested_dataclass_boundary() -> None:
    """Each module in _TARGET_FILES that defines an activity + nested-dataclass return
    must NOT use ``from __future__ import annotations`` (footgun F1).
    """
    violations: list[str] = []

    for rel_path in _TARGET_FILES:
        abs_path = Path(__file__).resolve().parents[2].parent / rel_path
        if not abs_path.exists():
            # Allow the test to keep passing if a file is renamed/moved —
            # the linter that watches the list ensures it stays in sync.
            continue
        tree = parse_file(abs_path)
        if not _has_activity_defn(tree):
            continue
        if not _has_future_annotations(tree):
            continue
        dataclass_names = _dataclass_names_in_module(tree)
        if len(dataclass_names) < 2:
            continue
        nested = _dataclass_with_nested_dataclass_field(tree, dataclass_names)
        if nested:
            violations.append(
                f"{rel_path} — uses `from __future__ import annotations` and "
                f"contains dataclass(es) {nested!r} with nested-dataclass field(s). "
                f"This crashes the workflow sandbox with NameError during "
                f"`get_type_hints` (footgun F1). Remove the future annotations "
                f"import and ensure inner dataclasses are defined before "
                f"outer ones."
            )

    assert not violations, (
        "R-JSON footgun F1 — nested dataclass + PEP 563 in workflow boundary:\n"
        + "\n".join(f"  - {v}" for v in violations)
        + "\n\nSee tests/architecture/test_r_json_nested_dataclass.py docstring."
    )
