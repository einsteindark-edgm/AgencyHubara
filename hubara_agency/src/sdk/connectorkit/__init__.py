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
"""
from __future__ import annotations

from src.platform.attribution import (
    AttributionReadPort as AttributionReadPort,
    AttributionSession as AttributionSession,
    FilesystemAttributionStore as FilesystemAttributionStore,
    InMemoryAttributionStore as InMemoryAttributionStore,
)
from src.sdk.connectorkit.ports import (
    AudioTranscriptionPort as AudioTranscriptionPort,
    CatalogPort as CatalogPort,
    CheckoutVerificationPort as CheckoutVerificationPort,
    CustomerScoringPort as CustomerScoringPort,
    ImageVisionPort as ImageVisionPort,
    MetaCatalogPort as MetaCatalogPort,
    OrderCommandPort as OrderCommandPort,
    OrderQueryPort as OrderQueryPort,
    OrderRegistrationPort as OrderRegistrationPort,
    get_audio_transcription_port as get_audio_transcription_port,
    get_catalog_client as get_catalog_client,
    get_customer_scoring_port as get_customer_scoring_port,
    get_image_vision_port as get_image_vision_port,
    get_order_command_port as get_order_command_port,
    get_order_query_port as get_order_query_port,
    get_order_registration_port as get_order_registration_port,
)
