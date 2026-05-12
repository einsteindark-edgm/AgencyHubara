"""Anti-patterns — DEHA layout invariants.

DEHA forbids three layout anti-patterns explicitly:
  #16: `src/core/`, `src/shared/`, `src/common/` — use `src/platform/` instead.
  #17: `src/domains/<agent>/` — agents are siblings (`src/<agent>/`),
       not children of a `domains/` umbrella.

It also forbids a Python-specific anti-pattern that appears repeatedly in this
repo's history: `from hubara_agency.src.X import ...`. The package root is
`hubara_agency/`, so the import path must always start with `src.`.

Finally, tool classes must end in `Tool` (positive naming rule). This is a
small but useful guard for the merger and worker.py auto-registration.

Tests:
  #11 — `test_no_forbidden_top_level_packages`
  #12 — `test_no_hubara_agency_src_prefix_imports`
  #15 — `test_tool_subclasses_end_with_Tool`
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture.conftest import (
    AGENT_TOOLS_GLOB,
    FORBIDDEN_TOP_LEVEL_PACKAGES,
    SRC_ROOT,
    iter_agent_files,
    parse_file,
    relative_to_hubara,
)


# ----------------------------------------------------------------------------
# Test #11 — directorios prohibidos bajo src/.
# ----------------------------------------------------------------------------

def test_no_forbidden_top_level_packages() -> None:
    """No deben existir `src/core/`, `src/shared/`, `src/common/`, `src/domains/`."""
    violations: list[str] = []
    for name in FORBIDDEN_TOP_LEVEL_PACKAGES:
        candidate = SRC_ROOT / name
        if candidate.exists():
            violations.append(
                f"src/{name}/ exists — DEHA forbids it. "
                f"Use src/platform/ for cross-agent infra and place agents as siblings."
            )
    assert not violations, (
        "DEHA anti-pattern #16/#17 violation:\n  " + "\n  ".join(violations)
    )


# ----------------------------------------------------------------------------
# Test #12 — imports con prefijo `hubara_agency.src.` no permitidos.
# ----------------------------------------------------------------------------

def test_no_hubara_agency_src_prefix_imports() -> None:
    """Ningún archivo .py debe importar como `hubara_agency.src.X` — debe ser `src.X`."""
    violations: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.parts and "dashboard" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if (
                stripped.startswith("from hubara_agency.src")
                or stripped.startswith("import hubara_agency.src")
            ):
                violations.append(f"{relative_to_hubara(path)}:{lineno} → `{stripped[:80]}`")
    # También cubrir tests/ ya que la convención aplica al repo entero.
    tests_root = SRC_ROOT.parent / "tests"
    for path in tests_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if (
                stripped.startswith("from hubara_agency.src")
                or stripped.startswith("import hubara_agency.src")
            ):
                violations.append(f"{relative_to_hubara(path)}:{lineno} → `{stripped[:80]}`")
    assert not violations, (
        "Import-path anti-pattern — use `from src.X` (not `from hubara_agency.src.X`):\n  "
        + "\n  ".join(violations)
    )


# ----------------------------------------------------------------------------
# Test #15 — tool class names end with `Tool`.
# ----------------------------------------------------------------------------

def test_tool_subclasses_end_with_Tool() -> None:
    """Toda clase declarada en `tools/*.py` que hereda de `ToolBase` debe
    terminar en `Tool`. Mantiene consistencia con `register_tool_extension`
    y con la convención de naming del merger.
    """
    violations: list[str] = []
    for path in iter_agent_files(AGENT_TOOLS_GLOB):
        if path.name == "__init__.py":
            continue
        rel = relative_to_hubara(path)
        tree = parse_file(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.append(base.attr)
            if "ToolBase" not in base_names:
                continue
            if not node.name.endswith("Tool"):
                violations.append(
                    f"{rel}:{node.lineno} class `{node.name}` extends ToolBase "
                    f"but does not end with `Tool`"
                )
    assert not violations, (
        "Naming anti-pattern — ToolBase subclasses must end with `Tool`:\n  "
        + "\n  ".join(violations)
    )
