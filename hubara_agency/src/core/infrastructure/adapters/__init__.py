"""Default adapters que cumplen los Protocols de `src/core/ports/`.

Cada adapter es un wrapper fino sobre las funciones modulo-level existentes
(brains.py, registries.py, whatsapp/client.py). No reescriben nada — solo
exponen el shape de clase para que `isinstance(impl, Port)` funcione contra
`@runtime_checkable`.
"""
from __future__ import annotations

from src.core.infrastructure.adapters.brain_loader_adapter import DefaultBrainLoader
from src.core.infrastructure.adapters.tool_registry_adapter import DefaultToolRegistry
from src.core.infrastructure.adapters.whatsapp_gateway_adapter import (
    DefaultWhatsAppGateway,
)

__all__ = [
    "DefaultBrainLoader",
    "DefaultToolRegistry",
    "DefaultWhatsAppGateway",
]
