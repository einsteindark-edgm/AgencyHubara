"""R-DIP #10 enforcer — anti-pattern: importing a workflow class from a
sibling agent module.

Detects the bug that motivated ADR-2026-05-19 (the precursor to
ADR-2026-05-20 declarative orchestration). Fails CI if any agent module
imports from a sibling agent's ``workflows.*`` or ``contracts.*`` submodule.

Canonical rule: cross-agent workflow dispatch MUST use either
    - ``get_workflow_name(plugin, worker)`` + string dispatch, OR
    - ``dispatch_event_activity`` + a manifest transition (Level 3)

Never import a sibling's class. Common shared types (events, DTOs) live in
``plugins/<plugin>/shared/contracts/`` — importing from shared/ is fine.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGINS_DIR = _REPO_ROOT / "src" / "plugins"

# Regex for "src.plugins.<plugin>.agent.<agent>.<submodule>" imports.
_PATTERN = re.compile(
    r"^src\.plugins\.(?P<plugin>[a-z_]+)\.agent\.(?P<agent>[a-z_]+)\.(?P<sub>[a-z_]+)(?:\..*)?$"
)

# Submodules that count as "internal implementation" of an agent and are
# NEVER OK to import across siblings. ``contracts`` was historically a leak
# (Transfer/Schedule decisions); ``workflows`` is the canonical example.
_FORBIDDEN_SUBMODULES = {"workflows", "contracts", "use_cases", "tools", "activities"}


def _iter_agent_python_files() -> list[Path]:
    """Every .py file under ``src/plugins/<plugin>/agent/<agent>/``."""
    out: list[Path] = []
    if not _PLUGINS_DIR.exists():
        return out
    for plugin_dir in _PLUGINS_DIR.iterdir():
        if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
            continue
        agent_root = plugin_dir / "agent"
        if not agent_root.exists():
            continue
        for agent_dir in agent_root.iterdir():
            if not agent_dir.is_dir():
                continue
            for py in agent_dir.rglob("*.py"):
                if "__pycache__" in py.parts:
                    continue
                out.append(py)
    return out


def _module_imports(py: Path) -> list[str]:
    """Module-level (top-of-file) ``from X import Y`` paths, as dotted strings."""
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    imports: list[str] = []
    # Only inspect top-level ImportFrom; ignore nested in functions/branches.
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    # And also imports inside `with workflow.unsafe.imports_passed_through():`
    # blocks (workflows put their cross-module deps there). Those count as
    # module-level for our purposes — they execute at module load.
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for stmt in node.body:
                if isinstance(stmt, ast.ImportFrom) and stmt.module:
                    imports.append(stmt.module)
    return imports


def test_no_agent_imports_sibling_agent_module() -> None:
    """Fail if any agent file imports from a sibling agent's forbidden submodule."""
    violations: list[str] = []

    for py in _iter_agent_python_files():
        rel = py.relative_to(_PLUGINS_DIR)
        parts = rel.parts
        # parts = (<plugin>, "agent", <agent>, ...)
        if len(parts) < 3 or parts[1] != "agent":
            continue
        own_plugin, own_agent = parts[0], parts[2]

        for imp in _module_imports(py):
            m = _PATTERN.match(imp)
            if not m:
                continue
            target_plugin = m.group("plugin")
            target_agent = m.group("agent")
            target_sub = m.group("sub")

            # Cross-sibling? (same plugin, different agent, forbidden submodule)
            same_plugin = target_plugin == own_plugin
            different_agent = target_agent != own_agent
            forbidden = target_sub in _FORBIDDEN_SUBMODULES

            if same_plugin and different_agent and forbidden:
                violations.append(
                    f"{rel} imports `{imp}` — sibling agent {target_sub}. "
                    f"Use shared/contracts/ events + dispatch_event_activity "
                    f"(ADR-2026-05-20) or get_workflow_name (ADR-2026-05-19)."
                )

    assert violations == [], (
        "R-DIP #10 violations — cross-agent imports detected:\n  "
        + "\n  ".join(violations)
    )


def test_no_platform_imports_agent_workflows() -> None:
    """Fail if any module under src.platform imports a workflow class from an agent.

    ``platform/`` orchestrates agents; importing from agents reverses the
    dependency arrow (R-DIP #9). Local imports inside activity bodies were
    documented exceptions pre-ADR-2026-05-20; this test verifies they're gone.
    """
    platform_root = _REPO_ROOT / "src" / "platform"
    if not platform_root.exists():
        return

    pattern_agent_workflow = re.compile(
        r"^src\.plugins\.[a-z_]+\.agent\.[a-z_]+\.workflows(?:\..*)?$"
    )
    violations: list[str] = []

    for py in platform_root.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if pattern_agent_workflow.match(node.module):
                    violations.append(
                        f"{py.relative_to(_REPO_ROOT)} imports `{node.module}` "
                        f"— platform must not import agent workflow classes "
                        f"(use get_workflow_name + string dispatch, "
                        f"ADR-2026-05-19 §4)."
                    )

    assert violations == [], (
        "R-DIP #9 violations — platform imports agent workflow classes:\n  "
        + "\n  ".join(violations)
    )
