"""Puerto de visión de imágenes (Protocol — DEHA-friendly).

Espejo de ``platform/audio/port.AudioTranscriptionPort``. Describe + clasifica
una imagen inbound de WhatsApp. El agente conversacional (DeepSeek) es
text-only; este puerto delega el "ver" a un modelo multimodal (Gemini) y
devuelve texto que se reinyecta a la conversación.

Implementations:

* ``LiteLLMVisionAdapter`` — Gemini multimodal vía el proxy litellm
* ``_FakeVisionAdapter`` (tests)
* ``_NullVisionAdapter`` (visión deshabilitada)
"""
from __future__ import annotations

from typing import Protocol

from src.platform.vision.dtos import VisionRequest, VisionResult


class ImageVisionPort(Protocol):
    """Describe + clasifica una imagen inbound de WhatsApp."""

    name: str

    async def describe(self, request: VisionRequest) -> VisionResult:
        """Descarga el media de Meta, lo describe y lo clasifica.

        Implementations deben:
          1. Descargar bytes via ``meta_media_fetcher.fetch_media_bytes``.
          2. Validar tamaño ≤ ``max_bytes`` ANTES del API call.
          3. Llamar al modelo multimodal (Gemini) pidiéndole TIPO +
             DESCRIPCION.
          4. Devolver ``VisionResult`` con description + kind + ok.

        NO debe raisear excepciones — todo error va en ``result.error``. El
        caller (ingest) decide el fallback según ``result.ok``.
        """
        ...
