"""Port: BrainLoaderPort.

Describe la capability de cargar el "cerebro compartido" (identity/knowledge/
instructions) de un agente desde una fuente externa (por ahora filesystem).

El adapter default (`DefaultBrainLoader`) lee del filesystem; en el futuro
podria reemplazarse por uno que lea de S3, ConfigMap, etc., sin tocar
los call-sites en domain/application.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class BrainLoaderPort(Protocol):
    """Carga los archivos de "brain" de un agente y los devuelve como strings."""

    def load(
        self,
        brain_dir: Path,
        files: tuple[str, ...] = ("identity.md", "knowledge.md", "instructions.md"),
    ) -> list[str]:
        """Lee los `files` desde `brain_dir`, retorna su contenido como list[str].

        Files que no existen son ignorados silenciosamente.
        """
        ...
