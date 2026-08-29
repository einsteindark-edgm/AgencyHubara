"""Los 9 ports de capability existentes + sus factories, promovidos al SDK.

Cada port es un ``typing.Protocol`` (structural — el adapter no hereda nada)
y cada factory resuelve el adapter REAL según la configuración del
deployment (composición con ``@lru_cache`` — R-STATELESS). Hoy el binding es
estático (Medusa para commerce, litellm para audio/vision/scoring); el
registry config-driven por env (``CONNECTOR_ORDERS=<vendor>``) entra cuando
exista el segundo vendor de un port (F-SDK-4b, plan §4.6).

Migración pendiente y anotada (no "descubrirla"): los ADAPTERS Medusa hoy
viven mezclados con los ports en ``src/platform/{orders,catalog,medusa}/``;
su mudanza física a ``platform/connectors/medusa/`` es F-SDK-4b. P-31 ya
congela el acople: ningún plugin NUEVO puede tocar esos módulos.
"""
from __future__ import annotations

from src.platform.audio.composition import (
    get_audio_transcription_port as get_audio_transcription_port,
)
from src.platform.carts.composition import (
    get_web_cart_reader as get_web_cart_reader,
)
from src.platform.carts.port import (
    FakeWebCartReader as FakeWebCartReader,
    NullWebCartReader as NullWebCartReader,
    WebCartItem as WebCartItem,
    WebCartReaderPort as WebCartReaderPort,
    WebCartSnapshot as WebCartSnapshot,
)
from src.platform.audio.port import (
    AudioTranscriptionPort as AudioTranscriptionPort,
)
from src.platform.catalog.checkout_port import (
    CheckoutVerificationPort as CheckoutVerificationPort,
)
from src.platform.catalog.composition import (
    get_catalog_client as get_catalog_client,
)
from src.platform.catalog.port import (
    CatalogPort as CatalogPort,
)
from src.platform.customer_scoring.composition import (
    get_customer_scoring_port as get_customer_scoring_port,
)
from src.platform.customer_scoring.port import (
    CustomerScoringPort as CustomerScoringPort,
)
from src.platform.meta_catalog.port import (
    MetaCatalogPort as MetaCatalogPort,
)
from src.platform.orders.command_port import (
    OrderCommandPort as OrderCommandPort,
)
from src.platform.orders.composition import (
    get_order_command_port as get_order_command_port,
    get_order_query_port as get_order_query_port,
    get_order_registration_port as get_order_registration_port,
)
from src.platform.orders.port import (
    OrderRegistrationPort as OrderRegistrationPort,
)
from src.platform.orders.query_port import (
    OrderQueryPort as OrderQueryPort,
)
from src.platform.vision.composition import (
    get_image_vision_port as get_image_vision_port,
)
from src.platform.vision.port import (
    ImageVisionPort as ImageVisionPort,
)
