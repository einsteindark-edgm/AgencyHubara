"""Composition root del vision layer.

Default: **alias `gemini-multimodal` del proxy litellm** (mismo proxy
``API_BASE_LLMLITE`` que usa el agente y el layer de audio; el alias vive en
``exoclaw-temporal/litellm_config.yaml`` y hoy resuelve a Gemini Flash-Lite).
Cambiar el modelo es editar ese alias en el config del proxy.

Modos (``IMAGE_VISION_PROVIDER``):

  auto / litellm   # default — Gemini multimodal via proxy
  fake             # adapter de tests, descripción sintética (sin red)
  off / null       # visión deshabilitada — el ingest cae al placeholder
                   # "[el cliente envió una imagen]" (comportamiento previo)

Modelo configurable:
  IMAGE_VISION_MODEL=litellm_proxy/gemini-multimodal  # default (alias del proxy)
  IMAGE_VISION_MODEL=gemini/gemini-2.5-flash          # SOLO con API_BASE="" (directo)

  GOTCHA (bug prod 2026-07): contra el proxy el prefijo ``litellm_proxy/`` es
  obligatorio — un prefijo nativo (``gemini/...``) postea al endpoint nativo
  de Google sobre el proxy y muere con 404. Guard:
  tests/platform/test_multimodal_via_proxy.py.

API base / key: ver ``litellm_adapter.LiteLLMVisionAdapter.from_env``.
``WHATSAPP_ACCESS_TOKEN`` es required para descargar el media de Meta.
"""
from __future__ import annotations

import os
from functools import lru_cache

import structlog

from src.platform.vision.dtos import (
    VISION_KIND_OTHER,
    VISION_KIND_PAYMENT_RECEIPT,
    VISION_KIND_PRODUCT_PHOTO,
    VisionRequest,
    VisionResult,
)
from src.platform.vision.litellm_adapter import LiteLLMVisionAdapter
from src.platform.vision.port import ImageVisionPort

logger = structlog.get_logger()


class _NullVisionAdapter:
    """Adapter no-op cuando la visión está deshabilitada. Devuelve
    ``error="no_provider_configured"``; el ingest cae al placeholder."""

    name = "null"

    async def describe(self, request: VisionRequest) -> VisionResult:
        return VisionResult(
            description="",
            ok=False,
            error="no_provider_configured",
            provider=self.name,
        )


class _FakeVisionAdapter:
    """Adapter para tests / dev sin conexión a Gemini.

    Clasifica por marcador en el ``media_id`` para poder ejercitar ambas
    ramas del ingest: si el id contiene "receipt"/"comprobante" → comprobante
    de pago; si contiene "product"/"vela" → foto de producto; si no → otro.
    """

    name = "fake"

    async def describe(self, request: VisionRequest) -> VisionResult:
        mid = request.media_id.lower()
        if "receipt" in mid or "comprobante" in mid or "pago" in mid:
            return VisionResult(
                description="Comprobante de transferencia por $34.000 (ref 1234)",
                ok=True,
                kind=VISION_KIND_PAYMENT_RECEIPT,
                is_payment_receipt=True,
                provider=self.name,
                cost_usd_estimate=0.0,
                latency_ms=10,
            )
        if "product" in mid or "vela" in mid:
            return VisionResult(
                description="Foto de una vela artesanal color rosado",
                ok=True,
                kind=VISION_KIND_PRODUCT_PHOTO,
                is_payment_receipt=False,
                provider=self.name,
                cost_usd_estimate=0.0,
                latency_ms=10,
            )
        return VisionResult(
            description=f"[FAKE VISION de media={request.media_id}]",
            ok=True,
            kind=VISION_KIND_OTHER,
            is_payment_receipt=False,
            provider=self.name,
            cost_usd_estimate=0.0,
            latency_ms=10,
        )


@lru_cache(maxsize=1)
def get_image_vision_port() -> ImageVisionPort:
    """Devuelve el port singleton según env.

    Default: ``LiteLLMVisionAdapter`` apuntando al alias
    ``litellm_proxy/gemini-multimodal`` via el proxy ``API_BASE_LLMLITE``.
    """
    provider = (os.getenv("IMAGE_VISION_PROVIDER") or "auto").lower()

    if provider == "fake":
        logger.info("vision.composition.using_fake_adapter")
        return _FakeVisionAdapter()

    if provider in {"off", "null", "disabled"}:
        logger.info("vision.composition.disabled")
        return _NullVisionAdapter()

    if provider in {"auto", "litellm"}:
        adapter = LiteLLMVisionAdapter.from_env()
        logger.info(
            "vision.composition.litellm_configured",
            model=adapter._model,
            api_base=adapter._api_base or "(litellm default)",
        )
        return adapter

    logger.warning(
        "vision.composition.unknown_provider",
        provider=provider,
        falling_back_to="null",
    )
    return _NullVisionAdapter()
