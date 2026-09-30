"""Verificador de fotos: ¿la foto del cliente es el MISMO diseño que alguno
de estos candidatos del catálogo, o ninguno?

Los candidatos son los productos más parecidos por embedding (``embeddings``);
el modelo ve la foto del cliente y UNA hoja con una fila numerada por
candidato (``images.candidate_sheet``) y contesta el número o null. Medido en
la investigación del 2026-09-29/30 con 37 casos (capturas de nuestro catálogo,
otra foto del mismo diseño, trampas como la captura de otra tienda o un nombre
parecido, y candidatos SIN el producto verdadero, donde debe decir null):
``gemini-3.5-flash-lite`` acertó 74 de 74 en dos corridas, mediana ~2 s, máximo
5,7 s, ~US$0,0008 por foto. Con TODO el catálogo en una hoja los modelos
baratos inventaban parecidos: por eso primero los 5 más cercanos.

Adaptadores: ``LiteLLMPhotoMatchAdapter`` (alias ``gemini-photo-match`` del
proxy), ``FakePhotoMatchAdapter`` (tests: elige el candidato que trae
exactamente los mismos bytes) y ``NullPhotoMatchAdapter``. Ninguno lanza.
"""
from __future__ import annotations

import base64
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import litellm
import structlog

from src.platform.config import API_BASE_LLMLITE
from src.platform.vision.images import candidate_sheet
from src.platform.vision.json_answer import json_object

logger = structlog.get_logger()

# El prompt con el que se midió (investigación, e4_topk.py), con el número de
# candidatos en vez del 5 fijo.
_PROMPT = (
    "Te muestro dos imágenes.\n"
    "IMAGEN 1: lo que un cliente nos envió por WhatsApp (foto, captura de pantalla o reenvío).\n"
    "IMAGEN 2: {n} productos candidatos de nuestro catálogo. Cada FILA es un producto, con su "
    "número (1 a {n}) a la izquierda y dos fotos del MISMO producto (pueden ser colores o escenas "
    "distintas).\n"
    "Tarea: decide si el producto principal de la IMAGEN 1 es el MISMO diseño que alguno de los {n} "
    "candidatos. Compara forma, figura y relieves; el color y el fondo pueden cambiar. Ignora el "
    "texto de la IMAGEN 1. Si ninguno es el mismo diseño, o la IMAGEN 1 no muestra una vela, "
    "responde null. Es preferible null a un número equivocado.\n"
    'Responde SOLO JSON: {{"numero": <1-{n} o null>, "motivo": "<una frase corta>"}}'
)


@dataclass(frozen=True)
class PhotoPick:
    """Lo que dijo el verificador.

    ``number``: el candidato (1..N) que es el mismo diseño; None = ninguno (o
    no se pudo saber: mirar ``ok``). ``reason``: la frase del modelo, solo
    para la traza.
    """

    ok: bool
    number: int | None = None
    reason: str | None = None
    error: str | None = None
    latency_ms: int | None = None


class PhotoMatchPort(Protocol):
    """¿Cuál de los candidatos es el mismo diseño que la foto? Nunca lanza."""

    name: str

    async def pick_same_design(
        self, photo: bytes, mime_type: str, candidates: Sequence[Sequence[bytes]]
    ) -> PhotoPick: ...


def _mime(mime_type: str) -> str:
    mime = (mime_type or "").lower().split(";")[0].strip()
    return mime if mime.startswith("image/") and mime != "image/jpg" else "image/jpeg"


class LiteLLMPhotoMatchAdapter:
    """El verificador por el proxy litellm (``litellm_proxy/gemini-photo-match``)."""

    def __init__(
        self,
        model: str = "litellm_proxy/gemini-photo-match",
        api_base: str | None = None,
        api_key: str | None = None,
        timeout_s: float = 8.0,
    ) -> None:
        self._model = model
        self._api_base = api_base
        self._api_key = api_key
        self._timeout_s = timeout_s
        self.name = model.replace("/", "_")

    @classmethod
    def from_env(cls) -> LiteLLMPhotoMatchAdapter:
        """``PHOTO_MATCH_MODEL`` (default el alias del proxy); api base y key
        igual que la visión."""
        model = os.getenv("PHOTO_MATCH_MODEL") or "litellm_proxy/gemini-photo-match"
        api_base = (
            os.getenv("IMAGE_VISION_API_BASE")
            if os.getenv("IMAGE_VISION_API_BASE") is not None
            else API_BASE_LLMLITE
        )
        api_key = os.getenv("IMAGE_VISION_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("LITELLM_API_KEY")
        if not api_key and model.startswith("litellm_proxy/"):
            api_key = "no-key"
        return cls(model=model, api_base=api_base or None, api_key=api_key or None)

    async def pick_same_design(
        self, photo: bytes, mime_type: str, candidates: Sequence[Sequence[bytes]]
    ) -> PhotoPick:
        n = len(candidates)
        if not n:
            return PhotoPick(ok=True)
        sheet = candidate_sheet(candidates)
        started = time.time()
        try:
            response = await litellm.acompletion(
                model=self._model,
                api_base=self._api_base,
                api_key=self._api_key,
                temperature=0,
                max_tokens=256,
                response_format={"type": "json_object"},
                timeout=self._timeout_s,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _PROMPT.format(n=n)},
                            {"type": "image_url", "image_url": {"url": _data_uri(photo, _mime(mime_type))}},
                            {"type": "image_url", "image_url": {"url": _data_uri(sheet, "image/jpeg")}},
                        ],
                    }
                ],
            )
            raw = response.choices[0].message.content
        except Exception as exc:  # noqa: BLE001 — el puerto nunca lanza
            logger.warning("photo_match.error", model=self._model, error_type=type(exc).__name__)
            return PhotoPick(ok=False, error=f"provider_error: {type(exc).__name__}", latency_ms=_ms(started))
        latency_ms = _ms(started)
        data = json_object(raw)
        if data is None or "numero" not in data:
            return PhotoPick(ok=False, error="bad_response_shape", latency_ms=latency_ms)
        reason = data.get("motivo") if isinstance(data.get("motivo"), str) else None
        number = data.get("numero")
        if number is None:
            return PhotoPick(ok=True, reason=reason, latency_ms=latency_ms)
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= n:
            return PhotoPick(ok=False, error="number_out_of_range", reason=reason, latency_ms=latency_ms)
        return PhotoPick(ok=True, number=number, reason=reason, latency_ms=latency_ms)


def _data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _ms(started: float) -> int:
    return int((time.time() - started) * 1000)


class FakePhotoMatchAdapter:
    """Doble oficial: elige el candidato que trae exactamente los mismos bytes
    que la foto (sin red); si ninguno, ninguno."""

    name = "fake"

    async def pick_same_design(
        self, photo: bytes, mime_type: str, candidates: Sequence[Sequence[bytes]]
    ) -> PhotoPick:
        for i, photos in enumerate(candidates, 1):
            if any(p == photo for p in photos):
                return PhotoPick(ok=True, number=i, reason="mismos bytes")
        return PhotoPick(ok=True)


class NullPhotoMatchAdapter:
    """Verificador apagado: nunca identifica."""

    name = "null"

    async def pick_same_design(
        self, photo: bytes, mime_type: str, candidates: Sequence[Sequence[bytes]]
    ) -> PhotoPick:
        return PhotoPick(ok=False, error="no_provider_configured")
