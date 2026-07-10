"""Composition del ciclo order-sentinel (R-STATELESS: el estado compartido
vive acá con @lru_cache, no en las activities). Construir NUNCA toca AWS —
el Boto3Launcher es perezoso; los tests monkeypatchean `acts.get_launcher`.
"""
from __future__ import annotations

from functools import lru_cache

from src.sdk.graphagentskit import Boto3Launcher, Launcher


@lru_cache(maxsize=1)
def get_launcher() -> Launcher:
    return Boto3Launcher()
