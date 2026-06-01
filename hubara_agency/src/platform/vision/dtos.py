"""DTOs JSON-safe para el pipeline de visión de imágenes inbound.

R-JSON: frozen dataclasses, sin tipos no-serializables (no datetime, no
Path; epoch ms + str). Espejo de ``platform/audio/dtos.py`` — diseñados para
cruzar el workflow boundary igual que los de audio.
"""
from __future__ import annotations

from dataclasses import dataclass


# Categorías que devuelve el clasificador de visión. El agente (DeepSeek) es
# text-only, así que Gemini "ve" la imagen y devuelve UNA de estas etiquetas
# + una descripción en texto que se reinyecta a la conversación.
VISION_KIND_PAYMENT_RECEIPT = "comprobante_pago"
VISION_KIND_PRODUCT_PHOTO = "foto_producto"
VISION_KIND_OTHER = "otro"


@dataclass(frozen=True)
class VisionRequest:
    """Pedido de descripción de una imagen inbound.

    Atributos:
      media_id: id del media en Meta (a descargar via Graph API — el URL
        temporal expira a los 5 min, igual que audio).
      mime_type: mime declarado por Meta (image/jpeg, image/png, image/webp).
      max_bytes: cap defensivo. Imágenes > este límite se rechazan con
        ``error="too_large"`` ANTES de gastar el API call. WhatsApp topa
        imágenes en ~5 MB; dejamos 8 MB de margen.
    """

    media_id: str
    mime_type: str = "image/jpeg"
    max_bytes: int = 8 * 1024 * 1024


@dataclass(frozen=True)
class VisionResult:
    """Resultado de la descripción + clasificación de la imagen.

    Atributos:
      description: descripción en español, una línea, orientada al vendedor.
        Vacío si error.
      ok: True si la descripción fue exitosa.
      kind: categoría detectada (``comprobante_pago`` | ``foto_producto`` |
        ``otro``).
      is_payment_receipt: shortcut == (kind == comprobante_pago). El ingest
        lo usa para decidir si asignar la conversación al humano.
      error: detalle si ok=False ("media_fetch_failed", "too_large",
        "provider_error", "empty", "bad_response_shape", "rate_limit",
        "no_provider_configured").
      provider: cuál adapter respondió (modelo o "fake"/"null").
      cost_usd_estimate: costo estimado para attribution.
      latency_ms: latencia del provider call.
    """

    description: str
    ok: bool
    kind: str = VISION_KIND_OTHER
    is_payment_receipt: bool = False
    error: str | None = None
    provider: str | None = None
    cost_usd_estimate: float | None = None
    latency_ms: int | None = None
