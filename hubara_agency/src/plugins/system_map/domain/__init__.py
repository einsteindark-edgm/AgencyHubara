"""Dominio del plugin system_map.

`contracts.py` — DTOs frozen (R-JSON safe).
`builder.py`   — construye `SystemGraph` desde manifests.
`orphan_detector.py` — flagging de huérfanos.
"""

from src.plugins.system_map.domain.builder import build_system_graph
from src.plugins.system_map.domain.contracts import (
    Edge,
    Node,
    OrphanReason,
    PluginSummary,
    Stats,
    SystemGraph,
)
from src.plugins.system_map.domain.orphan_detector import detect_orphans

__all__ = [
    "Edge",
    "Node",
    "OrphanReason",
    "PluginSummary",
    "Stats",
    "SystemGraph",
    "build_system_graph",
    "detect_orphans",
]
