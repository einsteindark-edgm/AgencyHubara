"""Integración del plugin `ads` con el Marketing API de Meta (Graph).

Subpaquete self-contained: OAuth (Facebook Login), token store, cliente Graph y
parser determinista. Ningún import de plugins sibling ni de `src.platform`
directo (se usa `src.sdk`). Vendors de red con import perezoso (gate de lazy
surface). Decisión de arquitectura: usamos el Marketing API estándar vía Meta
App propia — el MCP oficial de Meta gatea el acceso a clientes aprobados y
rechaza tokens de apps arbitrarias (spike 2026-06-30).
"""
