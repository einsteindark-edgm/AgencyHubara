"""Cache process-wide del mapeo display_id → backend_id de Medusa (L-2).

Por qué existe: el frontend navega orders por `display_id` ("#6", premortem
A1) y ambos adapters (query + command) lo resuelven con un page-scan de
`/admin/orders` + `/admin/draft-orders`. En Railway esos list endpoints
tardan 2-10s CADA UNO con variabilidad alta — aun pidiendo solo
`fields=id,display_id` (medido: el costo es el endpoint, no el payload).

Por qué es seguro cachear sin TTL:

* Medusa nunca reasigna un `display_id` a otra order (secuencia única).
* `convert_draft_to_order` PRESERVA el id — el mapeo no cambia al convertir.
* Un miss (id nuevo) simplemente cae al page-scan de siempre.

El cache se comparte entre `MedusaOrderQuery` y `MedusaOrderCommand`
(singletons por `lru_cache` en composition): la lista del dashboard lo
puebla gratis y los comandos (schedule/confirm) lo aprovechan — el flujo
real del operador SIEMPRE ve la lista antes de actuar sobre un pedido.
"""
from __future__ import annotations

# Cap defensivo: un dashboard ve miles de orders como mucho; si el proceso
# vive lo suficiente para superarlo, reseteamos (los misses re-pueblan).
_MAX_ENTRIES = 4096

_cache: dict[str, str] = {}


def get(display_id_str: str) -> str | None:
    """Backend id cacheado para un display_id normalizado ("6"), o None."""
    return _cache.get(display_id_str)


def put(display_id_str: str, backend_id: str) -> None:
    if len(_cache) >= _MAX_ENTRIES:
        _cache.clear()
    _cache[display_id_str] = backend_id


def clear() -> None:
    """Solo para tests."""
    _cache.clear()
