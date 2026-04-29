"""Adapters concretos para `BrainLoaderPort`.

`DefaultBrainLoader` envuelve `src.core.brains.load_brain` y cumple el Protocol
por duck typing. Se instancia en el composition root de cada dominio.
"""
from __future__ import annotations

from src.core.infrastructure.brains.default_loader import DefaultBrainLoader

__all__ = ["DefaultBrainLoader"]
