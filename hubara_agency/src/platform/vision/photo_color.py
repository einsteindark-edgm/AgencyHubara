"""¿De qué color es la vela de una foto del catálogo?

Caso del 2026-10-09: el bot mandó la foto de Encanto Silvestre (una ardilla
café) y en el selector solo salieron gris, amarillo y verde; el cliente citó
la foto («¿no viene en este color?») y nadie sabía de qué color era la vela.
Este lector ve la foto UNA vez y dice cuál de los colores del producto (lista
cerrada del catálogo) es el de la vela; quien llama lo guarda junto al índice
de fotos y no lo vuelve a preguntar.

Mismo modelo que el verificador de fotos (alias ``gemini-photo-match`` del
proxy: ``gemini-3.5-flash-lite``, ~2 s, centavos por mil fotos). Una foto con
varias velas de colores distintos, o un color que no está en la lista, es
``None``: es preferible no decir nada a decir un color equivocado.

Adaptadores: ``LiteLLMPhotoColorAdapter``, ``FakePhotoColorAdapter`` (tests:
color por bytes exactos) y ``NullPhotoColorAdapter``. Ninguno lanza.
"""
from __future__ import annotations

import base64
import os
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import litellm
import structlog

from src.platform.config import API_BASE_LLMLITE
from src.platform.observability.pricing import response_cost_usd
from src.platform.vision.json_answer import json_object

logger = structlog.get_logger()

_PROMPT = (
    "Esta es una foto de nuestro catálogo de velas artesanales. El producto es «{title}».\n"
    "¿De qué color es la vela «{title}» en la foto? Elige SOLO uno de estos colores del "
    "catálogo: {palette}.\n"
    "La luz cálida del fondo no cuenta: fíjate en la cera de la vela. Si en la foto hay varias "
    "velas de colores distintos y no sabes cuál es «{title}», o el color no se parece a ninguno "
    "de la lista, responde null. Es preferible null a un color equivocado.\n"
    'Responde SOLO JSON: {{"color": "<uno de la lista o null>"}}'
)


@dataclass(frozen=True)
class ColorPick:
    """Lo que dijo el lector.

    ``color``: uno de la paleta, con su nombre del catálogo; None = la foto
    no permite decirlo (si ``ok``) o no se pudo preguntar (si no).
    """

    ok: bool
    color: str | None = None
    error: str | None = None
    latency_ms: int | None = None
    # Lo que costó la lectura (USD); None = no se sabe o no hubo llamada.
    cost_usd: float | None = None


class PhotoColorPort(Protocol):
    """¿Cuál de los colores del producto es el de la vela de la foto? Nunca lanza."""

    name: str

    async def pick_color(
        self, photo: bytes, mime_type: str, *, title: str, palette: Sequence[str]
    ) -> ColorPick: ...


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold().strip()


def palette_color(answer: object, palette: Sequence[str]) -> str | None:
    """El color de la paleta que nombra la respuesta (sin tildes ni mayúsculas)."""
    if not isinstance(answer, str) or not answer.strip():
        return None
    wanted = _fold(answer)
    return next((color for color in palette if _fold(color) == wanted), None)


def _mime(mime_type: str) -> str:
    mime = (mime_type or "").lower().split(";")[0].strip()
    return mime if mime.startswith("image/") and mime != "image/jpg" else "image/jpeg"


class LiteLLMPhotoColorAdapter:
    """El lector por el proxy litellm (``litellm_proxy/gemini-photo-match``)."""

    def __init__(
        self,
        model: str = "litellm_proxy/gemini-photo-match",
        api_base: str | None = None,
        api_key: str | None = None,
        timeout_s: float = 6.0,
    ) -> None:
        self._model = model
        self._api_base = api_base
        self._api_key = api_key
        self._timeout_s = timeout_s
        self.name = model.replace("/", "_")

    @classmethod
    def from_env(cls) -> LiteLLMPhotoColorAdapter:
        """``PHOTO_COLOR_MODEL`` (default el alias del verificador); api base y
        key igual que la visión."""
        model = os.getenv("PHOTO_COLOR_MODEL") or "litellm_proxy/gemini-photo-match"
        api_base = (
            os.getenv("IMAGE_VISION_API_BASE")
            if os.getenv("IMAGE_VISION_API_BASE") is not None
            else API_BASE_LLMLITE
        )
        api_key = os.getenv("IMAGE_VISION_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("LITELLM_API_KEY")
        if not api_key and model.startswith("litellm_proxy/"):
            api_key = "no-key"
        return cls(model=model, api_base=api_base or None, api_key=api_key or None)

    async def pick_color(
        self, photo: bytes, mime_type: str, *, title: str, palette: Sequence[str]
    ) -> ColorPick:
        colors = [c for c in palette if isinstance(c, str) and c.strip()]
        if not colors:
            return ColorPick(ok=True)
        started = time.time()
        try:
            response = await litellm.acompletion(
                model=self._model,
                api_base=self._api_base,
                api_key=self._api_key,
                temperature=0,
                max_tokens=64,
                response_format={"type": "json_object"},
                timeout=self._timeout_s,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _PROMPT.format(title=title, palette=", ".join(colors))},
                            {"type": "image_url", "image_url": {"url": _data_uri(photo, _mime(mime_type))}},
                        ],
                    }
                ],
            )
            raw = response.choices[0].message.content
        except Exception as exc:  # noqa: BLE001 — el puerto nunca lanza
            logger.warning("photo_color.error", model=self._model, error_type=type(exc).__name__)
            return ColorPick(ok=False, error=f"provider_error: {type(exc).__name__}", latency_ms=_ms(started))
        latency_ms = _ms(started)
        cost = response_cost_usd(response, self._model)
        data = json_object(raw)
        if data is None or "color" not in data:
            return ColorPick(ok=False, error="bad_response_shape", latency_ms=latency_ms, cost_usd=cost)
        return ColorPick(ok=True, color=palette_color(data.get("color"), colors), latency_ms=latency_ms, cost_usd=cost)


class FakePhotoColorAdapter:
    """Doble oficial: el color que se le dio para esos bytes exactos (None si no)."""

    name = "fake"

    def __init__(self, colors: Mapping[bytes, str | None] | None = None) -> None:
        self._colors = dict(colors or {})
        self.calls: list[str] = []

    async def pick_color(
        self, photo: bytes, mime_type: str, *, title: str, palette: Sequence[str]
    ) -> ColorPick:
        self.calls.append(title)
        return ColorPick(ok=True, color=palette_color(self._colors.get(photo), palette))


class NullPhotoColorAdapter:
    """Sin lector: nunca dice un color."""

    name = "null"

    async def pick_color(
        self, photo: bytes, mime_type: str, *, title: str, palette: Sequence[str]
    ) -> ColorPick:
        return ColorPick(ok=False, error="disabled")


def _data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _ms(started: float) -> int:
    return int((time.time() - started) * 1000)


__all__ = [
    "ColorPick",
    "FakePhotoColorAdapter",
    "LiteLLMPhotoColorAdapter",
    "NullPhotoColorAdapter",
    "PhotoColorPort",
    "palette_color",
]
