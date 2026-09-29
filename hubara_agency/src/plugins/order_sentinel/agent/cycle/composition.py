"""Composition del ciclo order-sentinel (R-STATELESS: el estado compartido
vive acá con @lru_cache, no en las activities). Construir NUNCA toca AWS —
el Boto3Launcher es perezoso; los tests monkeypatchean `acts.get_launcher`
y `acts.get_reader_port`.
"""
from __future__ import annotations

from functools import lru_cache

from src.plugins.order_sentinel.agent.cycle.use_cases.readings import ORACLE_PROFILE
from src.sdk.connectorkit import PerceptionPort, get_perception_port
from src.sdk.graphagentskit import Boto3Launcher, Launcher


@lru_cache(maxsize=1)
def get_launcher() -> Launcher:
    return Boto3Launcher()


def get_reader_port() -> PerceptionPort:
    """El oráculo del lector de estado (Jev). `get_perception_port` ya
    guarda un adaptador por perfil; `PERCEPTION_PROVIDER` lo fuerza."""
    return get_perception_port(ORACLE_PROFILE)
