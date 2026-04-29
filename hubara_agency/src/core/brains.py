"""Carga generica del 'cerebro compartido' (`identity.md`, `knowledge.md`, `instructions.md`).

Cada dominio pasa su propio `brain_dir`; antes habia dos copias casi identicas en
`sales_whatsapp/service.py::_load_shared_brain` y
`remarketing_whatsapp/workflows/remarketing.py::_load_remarketing_brain`.
"""
from __future__ import annotations

from pathlib import Path

_DEFAULT_FILES = ("identity.md", "knowledge.md", "instructions.md")


def load_brain(brain_dir: Path, files: tuple[str, ...] = _DEFAULT_FILES) -> list[str]:
    context: list[str] = []
    for filename in files:
        filepath = brain_dir / filename
        if filepath.exists():
            context.append(filepath.read_text(encoding="utf-8"))
    return context
