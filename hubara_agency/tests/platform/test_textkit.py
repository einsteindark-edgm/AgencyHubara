"""Regla de oro del SDK: `src.sdk.textkit` re-exporta los guards PUROS del texto
LLM→cliente para que una TOOL pueda usarlos.

Por qué existe (run b06636a6): parte de estos guards ya salía por
`src.sdk.agentkit`, pero ese kit trae el turn loop (`workflow_helpers` →
`temporalio`) y el contrato R-DIP prohíbe que `tools/*.py` importe Temporal —
una tool no tenía camino limpio hacia ellos. Y la validación de un
`customer_message` DEBE vivir en la tool (activity), no en el workflow: un
regex cuyo veredicto decide commands es lógica de replay (L-21).

El check es por IDENTIDAD (`is`): la fachada re-exporta EL MISMO objeto que
platform, no una re-implementación.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SDK = Path(__file__).resolve().parents[2] / "src" / "sdk"


def test_textkit_reexports_the_customer_text_guards() -> None:
    import src.platform.llm_text_sanitizer as impl
    import src.sdk.textkit as kit

    assert kit.sanitize_llm_text is impl.sanitize_llm_text
    assert kit.keep_customer_safe_sentences is impl.keep_customer_safe_sentences
    assert kit.looks_like_admin_leak is impl.looks_like_admin_leak
    assert kit.breaks_human_persona is impl.breaks_human_persona


def test_textkit_only_depends_on_the_pure_sanitizer() -> None:
    """Su razón de ser: importable desde `tools/*.py`. Si un día importa otra
    cosa de platform (o Temporal), las tools vuelven a quedar sin camino."""
    path = _SDK / "textkit.py"
    assert path.is_file(), f"falta {path}"

    imported = {
        node.module
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module != "__future__"
    }

    assert imported == {"src.platform.llm_text_sanitizer"}


def test_the_sanitizer_behind_textkit_is_stdlib_only() -> None:
    sanitizer = _SDK.parent / "platform" / "llm_text_sanitizer.py"
    tree = ast.parse(sanitizer.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])

    assert roots <= {"__future__", "re", "dataclasses", "typing", "unicodedata"}, roots
