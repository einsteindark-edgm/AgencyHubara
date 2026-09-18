"""Composition root para infra cross-plugin de WhatsApp.

Singletons por proceso (`@lru_cache(maxsize=1)`) — R-STATELESS cumplido:
las activities NO mantienen module-level state; reciben la dep vía estas
factories.
"""
from __future__ import annotations

import os
import time
from functools import lru_cache

from src.platform.whatsapp.cost import (
    RateCard,
    effective_rate_card_version,
    load_rate_card_from_yaml,
)
from src.platform.whatsapp.templates.registry import (
    TemplateSpec,
    load_template_registry_from_yaml,
)


#: Override de EMERGENCIA de la versión del rate card. Normalmente NO se
#: setea: la tarjeta vigente se elige por fecha (ver `get_current_rate_card`).
_RATE_CARD_ENV_VAR: str = "WHATSAPP_RATE_CARD_VERSION"


@lru_cache(maxsize=8)
def _load_rate_card(version: str) -> RateCard:
    """Un YAML se lee una vez por proceso (son inmutables — R-STATELESS: el
    cache vive en el composition root, no en las activities)."""
    return load_rate_card_from_yaml(version)


def get_current_rate_card(now_ms: int | None = None) -> RateCard:
    """El rate card VIGENTE.

    Se elige POR FECHA entre los YAML de `rate_cards/` (`effective_from_ms`).
    Para publicar tarifas nuevas basta agregar el YAML con su fecha de
    vigencia: entra solo, sin flippear nada ni reiniciar workers.

    Antes (hasta 2026-09-18) era un default hardcodeado `co_2026q2_v1` + el
    env `WHATSAPP_RATE_CARD_VERSION`, que NO existía en SSM: el 1-oct-2026
    prod habría seguido con `service: 0` mientras Meta ya cobraba. Y el
    `lru_cache(maxsize=1)` congelaba la tarjeta hasta el próximo deploy.

    `WHATSAPP_RATE_CARD_VERSION`, si está, FUERZA esa versión (emergencia).
    `now_ms` es inyectable para tests/backfills; por default el reloj del
    proceso — NO llamar desde código de workflow (R-DET): solo activities,
    use cases y API.
    """
    forced = os.environ.get(_RATE_CARD_ENV_VAR, "").strip()
    if forced:
        return _load_rate_card(forced)
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    return _load_rate_card(effective_rate_card_version(now_ms))


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
