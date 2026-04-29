"""Ports formales (typing.Protocol) — DIP segun DEHA (R-DIP).

Cada Protocol describe una capability hacia infraestructura externa
(filesystem, HTTP, registries de tools). Los call-sites de application/domain
deben depender de estas abstracciones, no de las implementaciones concretas.

Las implementaciones concretas viven en `src/core/infrastructure/...` y son
adapters que cumplen estos Protocols por duck typing (o `isinstance` con
`@runtime_checkable`).
"""
from __future__ import annotations

from src.core.ports.brain_loader import BrainLoaderPort
from src.core.ports.tool_registry import ToolRegistryPort
from src.core.ports.whatsapp_gateway import WhatsAppGatewayPort

__all__ = [
    "BrainLoaderPort",
    "ToolRegistryPort",
    "WhatsAppGatewayPort",
]
