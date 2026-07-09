"""Composition root del plugin reengagement (R-STATELESS: el estado vive acá
con @lru_cache, nunca a nivel módulo de una activity)."""
from __future__ import annotations

from functools import lru_cache

from src.sdk.graphagentskit import Boto3Launcher, Launcher


@lru_cache(maxsize=1)
def get_launcher() -> Launcher:
    """El Launcher del bridge GraphAgents (vendor boto3 en prod; los tests
    monkeypatchean esta factory con un fake). Construir NUNCA toca AWS."""
    return Boto3Launcher()
