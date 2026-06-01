"""Platform — pipeline de visión de imágenes inbound de WhatsApp.

El agente conversacional (DeepSeek V4) es text-only. Cuando llega una imagen,
este layer la describe con un modelo multimodal (Gemini Flash-Lite via el
proxy litellm ya configurado) y reinyecta la descripción como texto — mismo
patrón que el de audio (transcripción → reinyección). Gemini "ve", DeepSeek
"vende".

Composición (espejo de ``platform/audio/``):

* ``port.ImageVisionPort`` — interfaz puerto/adaptador
* ``dtos.VisionRequest`` / ``VisionResult`` — DTOs R-JSON safe
* ``litellm_adapter.LiteLLMVisionAdapter`` — Gemini multimodal via proxy
* ``composition.get_image_vision_port`` — factory singleton

Clasificación: cada imagen se etiqueta ``comprobante_pago`` |
``foto_producto`` | ``otro``. El ingest usa ``is_payment_receipt`` para
asignar la conversación al humano (verificación de pago) antes de pasarla al
agente.

Default por env:

  IMAGE_VISION_PROVIDER=auto                       # default (litellm/Gemini)
  IMAGE_VISION_MODEL=gemini/gemini-2.5-flash-lite  # default
  IMAGE_VISION_API_BASE=$API_BASE_LLMLITE          # default (proxy)
  IMAGE_VISION_API_KEY=                            # opcional si el proxy maneja auth
  WHATSAPP_ACCESS_TOKEN=...                        # required para fetch media

Kill-switch: ``IMAGE_VISION_PROVIDER=off`` → el ingest cae al placeholder
"[el cliente envió una imagen]" (comportamiento previo a esta feature).
"""
from src.platform.vision.dtos import (
    VISION_KIND_OTHER,
    VISION_KIND_PAYMENT_RECEIPT,
    VISION_KIND_PRODUCT_PHOTO,
    VisionRequest,
    VisionResult,
)
from src.platform.vision.port import ImageVisionPort

__all__ = [
    "ImageVisionPort",
    "VisionRequest",
    "VisionResult",
    "VISION_KIND_PAYMENT_RECEIPT",
    "VISION_KIND_PRODUCT_PHOTO",
    "VISION_KIND_OTHER",
]
