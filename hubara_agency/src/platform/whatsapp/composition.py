"""Composition root para infra cross-plugin de WhatsApp.

Singletons por proceso (`@lru_cache(maxsize=1)`) — R-STATELESS cumplido:
las activities NO mantienen module-level state; reciben la dep vía estas
factories.
"""
from __future__ import annotations

import os
from functools import lru_cache

from src.platform.whatsapp.cost import RateCard, load_rate_card_from_yaml
from src.platform.whatsapp.templates.registry import (
    TemplateSpec,
    load_template_registry_from_yaml,
)


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


@lru_cache(maxsize=1)
def get_template_registry() -> dict[str, TemplateSpec]:
    """Catalog de templates aprobados por Meta para esta WABA.

    Lee `src/platform/whatsapp/templates/catalog.yaml`. Singleton — el catalog
    es estático por proceso. Cuando se aprueba un template nuevo en Meta
    Business Manager, agregar al YAML y redesplegar.

    NOTA: el catalog declara la `category` con la que el operador submitió
    el template a Meta. Si Meta re-categoriza (utility → marketing), el
    `pricing.category` del webhook delivery status es la fuente de verdad
    post-facto (lo capturamos en metadata.last_outbound.pricing y en el
    cost summary del episode).
    """
    return load_template_registry_from_yaml()
