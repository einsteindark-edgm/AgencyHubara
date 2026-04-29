"""Adapter default para `BrainLoaderPort`.

Lee del filesystem usando `src.core.brains.load_brain`. Es un Adapter delgado:
solo existe para que los call-sites de application/use_cases dependan del
Protocol y no de la funcion concreta (R-DIP).
"""
from __future__ import annotations

from pathlib import Path

from src.core.brains import load_brain


class DefaultBrainLoader:
    """Implementacion filesystem-based de `BrainLoaderPort`."""

    def load(
        self,
        brain_dir: Path,
        files: tuple[str, ...] = ("identity.md", "knowledge.md", "instructions.md"),
    ) -> list[str]:
        return load_brain(brain_dir, files)
