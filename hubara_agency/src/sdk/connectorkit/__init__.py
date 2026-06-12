"""ConnectorKit — los PORTS de capability hacia sistemas externos (F-SDK-4).

El lado *driven* del hexágono, formalizado: los plugins consumen CONTRATOS
(``typing.Protocol``) + factories de composición — jamás un client de vendor
(Medusa, Meta, ...) directo. El vendor es un detalle de deployment; cambiarlo
= otro adapter detrás del MISMO port, cero cambios en plugins.

Las 4 reglas del kit (docs/_sdk/07-connectorkit.md):

1. Plugins importan ports de ACÁ (P-31 congela los toques de vendor legacy).
2. Ningún port sin FAKE oficial (testear sin red ni credenciales).
3. Ningún adapter sin contract suite (la misma corre contra fake y vendor).
4. HTTP honesto: timeout por la CADENA del upstream; connect-error = "NO se
   aplicó" (502); read-timeout = "DESCONOCIDO, verificá antes de reintentar"
   (504) — lección L-1.

CARGA LAZY (premortem F-SDK, PEP 562): la atribución es stdlib-pura y se
importa eager; los 9 ports + factories se resuelven recién en el PRIMER
acceso al símbolo. Razón: las cadenas de composición de los ports arrastran
vendors pesados (medusa/litellm: medido +759 módulos y +5s de import) y un
consumidor liviano (ads solo necesita atribución) NO debe pagarlas. El guard
``tests/architecture/test_sdk_lazy_surface.py`` lo exige: importar este
módulo no carga ningún vendor.
"""
from __future__ import annotations

import importlib
from typing import Any

from src.platform.attribution import (
    AttributionReadPort as AttributionReadPort,
    AttributionSession as AttributionSession,
    FilesystemAttributionStore as FilesystemAttributionStore,
    InMemoryAttributionStore as InMemoryAttributionStore,
)

# Símbolo público → módulo que lo define. La fuente espejo (eager, para quien
# quiera todo explícito) es src/sdk/connectorkit/ports.py — mantener AMBOS en
# sync (el guard test compara).
_LAZY_EXPORTS: dict[str, str] = {
    # Ports (typing.Protocol):
    "AudioTranscriptionPort": "src.platform.audio.port",
    "CatalogPort": "src.platform.catalog.port",
    "CheckoutVerificationPort": "src.platform.catalog.checkout_port",
    "CustomerScoringPort": "src.platform.customer_scoring.port",
    "ImageVisionPort": "src.platform.vision.port",
    "MetaCatalogPort": "src.platform.meta_catalog.port",
    "OrderCommandPort": "src.platform.orders.command_port",
    "OrderQueryPort": "src.platform.orders.query_port",
    "OrderRegistrationPort": "src.platform.orders.port",
    # Factories de composición (el deployment decide el vendor):
    "get_audio_transcription_port": "src.platform.audio.composition",
    "get_catalog_client": "src.platform.catalog.composition",
    "get_customer_scoring_port": "src.platform.customer_scoring.composition",
    "get_image_vision_port": "src.platform.vision.composition",
    "get_order_command_port": "src.platform.orders.composition",
    "get_order_query_port": "src.platform.orders.composition",
    "get_order_registration_port": "src.platform.orders.composition",
}


def __getattr__(name: str) -> Any:  # PEP 562 — resolución lazy por símbolo
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value  # cache: el segundo acceso es un dict lookup
    return value


def __dir__() -> list[str]:
    return sorted([*globals().keys(), *_LAZY_EXPORTS.keys()])
