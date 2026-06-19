"""Checks de las **tools** (el TCK por-tool, no por-agente). Única fuente; la
consumen pytest (tests/tools, tests/architecture) y la CLI (`certify-tool`).

Reglas:
- `T-IMPL`     — `impl` con forma 'modulo:funcion' y el archivo existe.
- `T-DUR`      — una tool `outward` DEBE declarar `approval_required` (G-DUR).
- `G-AGNOSTIC` — la `impl` NO importa ningún runtime (langgraph/agentspan); eso
                 vive en `adapters/`. Se verifica por AST sobre el archivo fuente.
"""
from __future__ import annotations

import ast
from pathlib import Path

from sdk.tool_model import ToolContract

_RUNTIMES = ("langgraph", "langchain", "agentspan")


def check_impl_ref(c: ToolContract) -> list[str]:
    if ":" not in c.impl:
        return [f"[T-IMPL] impl '{c.impl}' debe ser 'modulo:funcion'"]
    return []


def check_outward_needs_approval(c: ToolContract) -> list[str]:
    if c.side_effect == "outward" and not c.approval_required:
        return [
            f"[T-DUR] tool '{c.id}' es side_effect=outward y no declara "
            "approval_required (toda acción outward requiere aprobación durable)"
        ]
    return []


def check_impl_is_agnostic(c: ToolContract, ga_root: Path) -> list[str]:
    """G-AGNOSTIC: la impl no puede importar un runtime (eso va en adapters/)."""
    if ":" not in c.impl:
        return []  # ya lo reporta T-IMPL
    mod = c.impl.split(":", 1)[0]
    f = ga_root / (mod.replace(".", "/") + ".py")
    if not f.exists():
        return [f"[T-IMPL] no encuentro la impl en disco: {f}"]
    try:
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
    except SyntaxError as e:  # noqa: PERF203
        return [f"[T-IMPL] {f} no parsea: {e}"]
    bad: set[str] = set()
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for m in mods:
            if any(m == r or m.startswith(r + ".") for r in _RUNTIMES):
                bad.add(m)
    if bad:
        return [
            f"[G-AGNOSTIC] impl '{c.impl}' importa runtime(s) {sorted(bad)} — "
            "movelo a tools/<id>/adapters/ y dejá la impl pura"
        ]
    return []


def run_tool_checks(c: ToolContract, ga_root: Path | None = None) -> dict:
    errors = check_impl_ref(c) + check_outward_needs_approval(c)
    if ga_root is not None:
        errors += check_impl_is_agnostic(c, ga_root)
    return {"errors": errors, "warnings": []}


def tool_level(c: ToolContract, ga_root: Path | None = None) -> str:
    """C0 (algo roto) · C1 (carga, warnings) · C2 (TCK verde)."""
    res = run_tool_checks(c, ga_root)
    if res["errors"]:
        return "C0"
    return "C2" if not res["warnings"] else "C1"
