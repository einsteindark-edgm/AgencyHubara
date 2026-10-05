"""Embeddings de imagen: una foto → un vector, para buscar las fotos del
catálogo más parecidas a la que mandó el cliente.

Modelo: ``gemini-embedding-2`` (multimodal, GA desde el 2026-04-22, sin fecha
de apagado; alias ``gemini-embedding`` del proxy litellm). Solo acepta PNG o
JPEG: cualquier foto viaja en JPEG (las del catálogo son WEBP). Con 768
dimensiones el proveedor ya devuelve el vector normalizado.

Medido en la investigación del 2026-09-29 (116 fotos del catálogo, capturas y
fotos reales de clientes): la foto verdadera quedó primera en todas las
capturas; con los 5 más parecidos + el verificador (``photo_match``), 74 de
74 casos bien. Probado el 2026-09-30 con el litellm del proxy (v1.86.2): el
vector que devuelve por litellm es el mismo que el de la API de Gemini directa
(coseno 1,0), o sea que la imagen viaja como imagen y no como texto.

Adaptadores: ``LiteLLMImageEmbeddingAdapter`` (proxy), ``FakeImageEmbeddingAdapter``
(tests: vector determinista por bytes) y ``NullImageEmbeddingAdapter``
(apagado). Ninguno lanza: cualquier falla es None.
"""
from __future__ import annotations

import base64
import hashlib
import math
import os
from typing import Any, Protocol

import litellm
import structlog

from src.platform.config import API_BASE_LLMLITE
from src.platform.observability.pricing import (
    image_price_usd,
    load_pricing_table,
    proxy_reported_cost_usd,
)
from src.platform.vision.images import to_jpeg

logger = structlog.get_logger()

#: Dimensiones del vector (las recomendadas: 768, 1536 o 3072).
EMBEDDING_DIMENSIONS = 768
#: El lado mayor de la foto que se manda (el proveedor la reduce igual).
_MAX_SIDE = 1024


class ImageEmbeddingPort(Protocol):
    """Una foto → su vector. Nunca lanza: una falla es None.

    ``model`` y ``dimensions`` dicen con qué se mide: un índice de fotos medido
    con otro modelo no sirve (``platform/catalog/photo_index``).
    """

    name: str

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, image_bytes: bytes, mime_type: str) -> list[float] | None: ...


def _vector(value: Any, dimensions: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != dimensions:
        return None
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value):
        return None
    return [float(x) for x in value]


class LiteLLMImageEmbeddingAdapter:
    """Embeddings por el proxy litellm (``litellm_proxy/gemini-embedding``)."""

    def __init__(
        self,
        model: str = "litellm_proxy/gemini-embedding",
        api_base: str | None = None,
        api_key: str | None = None,
        dimensions: int = EMBEDDING_DIMENSIONS,
        timeout_s: float = 10.0,
    ) -> None:
        self._model = model
        self._api_base = api_base
        self._api_key = api_key
        self._dimensions = dimensions
        self._timeout_s = timeout_s
        self.name = model.replace("/", "_")

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @classmethod
    def from_env(cls) -> LiteLLMImageEmbeddingAdapter:
        """``IMAGE_EMBEDDING_MODEL`` (default el alias del proxy); api base y
        key igual que la visión (``IMAGE_VISION_API_BASE`` / el proxy)."""
        model = os.getenv("IMAGE_EMBEDDING_MODEL") or "litellm_proxy/gemini-embedding"
        api_base = (
            os.getenv("IMAGE_VISION_API_BASE")
            if os.getenv("IMAGE_VISION_API_BASE") is not None
            else API_BASE_LLMLITE
        )
        api_key = os.getenv("IMAGE_VISION_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("LITELLM_API_KEY")
        # Igual que la visión: el cliente del proxy exige una key aunque el
        # proxy no tenga auth (el contenedor de la API no recibe llaves de LLM).
        if not api_key and model.startswith("litellm_proxy/"):
            api_key = "no-key"
        return cls(model=model, api_base=api_base or None, api_key=api_key or None)

    async def embed(self, image_bytes: bytes, mime_type: str) -> list[float] | None:
        vector, _cost = await self.embed_measured(image_bytes, mime_type)
        return vector

    async def embed_measured(self, image_bytes: bytes, mime_type: str) -> tuple[list[float] | None, float | None]:
        """El vector y lo que costó la llamada (USD; None = no se sabe o no
        hubo llamada). La foto del cliente lo usa para cobrarlo a la
        conversación; el índice del catálogo usa `embed`."""
        jpeg = to_jpeg(image_bytes, max_side=_MAX_SIDE)
        if jpeg is None:
            return None, None
        data_uri = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        try:
            response = await litellm.aembedding(
                model=self._model,
                api_base=self._api_base,
                api_key=self._api_key,
                input=[data_uri],
                dimensions=self._dimensions,
                timeout=self._timeout_s,
            )
            item = response.data[0]
            value = item["embedding"] if isinstance(item, dict) else getattr(item, "embedding", None)
        except Exception as exc:  # noqa: BLE001 — el puerto nunca lanza
            logger.warning("image_embedding.error", model=self._model, error_type=type(exc).__name__)
            return None, None
        # Google cobra este embedding POR IMAGEN, y el proxy (litellm 1.86.2)
        # reporta prompt_tokens=0 cuando la entrada es una imagen: tokens ×
        # tabla daría 0. Si el proxy no manda su costo, una imagen × imagePrice.
        cost = proxy_reported_cost_usd(response)
        if cost is None:
            cost = image_price_usd(self._model, load_pricing_table())
        vector = _vector(value, self._dimensions)
        if vector is None:
            logger.warning("image_embedding.bad_response", model=self._model)
        return vector, cost


class FakeImageEmbeddingAdapter:
    """Doble oficial: vector unitario determinista a partir de los bytes (la
    misma foto da el mismo vector; fotos distintas, vectores distintos)."""

    name = "fake"

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return "fake"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_measured(self, image_bytes: bytes, mime_type: str) -> tuple[list[float] | None, float | None]:
        """El doble no cobra: el vector y costo desconocido."""
        return await self.embed(image_bytes, mime_type), None

    async def embed(self, image_bytes: bytes, mime_type: str) -> list[float] | None:
        if to_jpeg(image_bytes, max_side=64) is None:
            return None
        values: list[float] = []
        counter = 0
        while len(values) < self._dimensions:
            digest = hashlib.sha256(image_bytes + counter.to_bytes(4, "big")).digest()
            values.extend((b - 127.5) / 127.5 for b in digest)
            counter += 1
        values = values[: self._dimensions]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]


class NullImageEmbeddingAdapter:
    """Embeddings apagados: nunca hay vector."""

    name = "null"
    model = "null"
    dimensions = EMBEDDING_DIMENSIONS

    async def embed(self, image_bytes: bytes, mime_type: str) -> list[float] | None:
        return None
