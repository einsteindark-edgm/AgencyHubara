"""Ninguna activity de un workflow reintenta para siempre (auditoría de Temporal, 2026-10-15).

Sin `retry_policy`, Temporal reintenta sin tope (cada 100 s como máximo): una
lectura del vault que falla siempre (un bug, un archivo roto) deja el workflow
colgado en silencio, con su id tomado, en vez de fallar a la vista. Remarketing
tenía 9 llamadas así. Cada `workflow.execute_activity`/`start_activity` pasa su
`retry_policy=` o un preset (`**OPCIONES`, que la trae).
"""
from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"


def _unbounded_calls() -> list[str]:
    found = []
    for path in sorted(_SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "execute_activity" not in source and "start_activity" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("execute_activity", "start_activity")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "workflow"
            ):
                continue
            keywords = {k.arg for k in node.keywords}
            if "retry_policy" not in keywords and None not in keywords:  # None = `**preset`
                found.append(f"{path.relative_to(_SRC.parent)}:{node.lineno}")
    return found


def test_every_workflow_activity_has_a_finite_retry_policy() -> None:
    assert _unbounded_calls() == []


def test_every_retry_preset_brings_its_policy() -> None:
    from src.platform.temporal import retry_policies as presets

    for name in ("_LLM_OPTIONS", "_LLM_CHAT_OPTIONS", "_TOOL_OPTIONS", "_CONV_OPTIONS", "LLM_ACTIVITY_OPTIONS"):
        policy = getattr(presets, name)["retry_policy"]
        assert policy.maximum_attempts > 0, name
