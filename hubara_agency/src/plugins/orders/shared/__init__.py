"""Contratos compartidos del plugin `orders` (cruzan el boundary workflow/HTTP).

Espejo de `src.plugins.chats.shared`: los completion events que el plugin
`orders` emite viven en `shared/contracts/events.py` para que el dispatcher
declarativo (ADR-2026-05-20) los rutee sin que ningún plugin sibling importe
código del otro (R-DIP).
"""
