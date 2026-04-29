"""Adapter default para `BrainLoaderPort`: delega en `src.core.brains.load_brain`.

No re-implementa la logica — solo la envuelve en una clase para que
`isinstance(impl, BrainLoaderPort)` con `@runtime_checkable` resuelva True.
"""
from __future__ import annotations

from pathlib import Path

from src.core.brains import load_brain


class DefaultBrainLoader:
    """Lee `identity.md`/`knowledge.md`/`instructions.md` del filesystem."""

    def load(
        self,
        brain_dir: Path,
        files: tuple[str, ...] = ("identity.md", "knowledge.md", "instructions.md"),
    ) -> list[str]:
        return load_brain(brain_dir, files)
