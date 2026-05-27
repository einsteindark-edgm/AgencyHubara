"""Composition root para infra cross-plugin de WhatsApp.

Por ahora solo provee el rate card cacheado. A medida que el HU-WA24H-001
agregue piezas (template registry, etc.), se agregan factories aquí.

Singleton por proceso (`@lru_cache(maxsize=1)`) — R-STATELESS cumplido:
las activities NO mantienen module-level state; reciben la dep vía esta
factory.
"""
from __future__ import annotations

import os
from functools import lru_cache

from src.platform.whatsapp.cost import RateCard, load_rate_card_from_yaml


#: Versión del rate card vigente. Cuando Meta actualiza pricing, se cambia
#: el env var sin código (e.g., a "co_2026q3_v1") en el deploy del cambio.
_DEFAULT_RATE_CARD_VERSION: str = "co_2026q2_v1"
_RATE_CARD_ENV_VAR: str = "WHATSAPP_RATE_CARD_VERSION"


@lru_cache(maxsize=1)
def get_current_rate_card() -> RateCard:
    """Carga el rate card vigente desde YAML.

    Lee la versión desde el env var `WHATSAPP_RATE_CARD_VERSION`. Si no está
    seteado, usa `co_2026q2_v1` (Colombia, abril-junio 2026).

    Cache singleton: el archivo YAML se lee una vez al startup. Si la versión
    cambia (cuando Meta sube precios), requiere redeploy — esto es deliberado
    para garantizar consistencia cross-worker en el mismo proceso.
    """
    version = os.environ.get(_RATE_CARD_ENV_VAR, _DEFAULT_RATE_CARD_VERSION)
    return load_rate_card_from_yaml(version)
