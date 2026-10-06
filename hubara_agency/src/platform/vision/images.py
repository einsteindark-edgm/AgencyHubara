"""Imágenes para comparar la foto del cliente con el catálogo (Pillow).

* ``to_jpeg``: las fotos del catálogo son WEBP y el embedding de imágenes de
  Gemini (``gemini-embedding-2``) solo acepta PNG o JPEG. Pasa cualquier foto a
  JPEG con el lado mayor acotado (una portada del catálogo mide 2560×1440) y la
  transparencia en blanco.
* ``candidate_sheet``: la hoja que ve el verificador. Una fila por candidato,
  su número a la izquierda y hasta dos fotos del mismo producto. Es el formato
  con el que el verificador acertó 74 de 74 casos (capturas, otra foto del
  mismo diseño, trampas y candidatos sin el producto verdadero) en la
  investigación del 2026-09-29: cada foto se estira a la celda, como allá.

Pillow se importa adentro de cada función: solo lo paga quien identifica fotos.
"""
from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Any

#: Tamaño del número de la fila en la hoja (en píxeles).
_LABEL_SIZE = 40


def _open_rgb(image_bytes: bytes) -> Any | None:
    """La imagen en RGB (la transparencia, en blanco), o None si no abre."""
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        white = Image.new("RGBA", image.size, (255, 255, 255, 255))
        white.alpha_composite(image)
        image = white
    return image.convert("RGB")


def _jpeg(image: Any, quality: int) -> bytes:
    out = io.BytesIO()
    image.save(out, "JPEG", quality=quality)
    return out.getvalue()


def to_jpeg(image_bytes: bytes, *, max_side: int = 1024, quality: int = 88) -> bytes | None:
    """La foto en JPEG con el lado mayor ≤ ``max_side``; None si no es imagen."""
    image = _open_rgb(image_bytes)
    if image is None:
        return None
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side))
    return _jpeg(image, quality)


def candidate_sheet(
    rows: Sequence[Sequence[bytes]],
    *,
    cell: tuple[int, int] = (480, 270),
    label_w: int = 60,
    quality: int = 88,
) -> bytes:
    """La hoja de candidatos en JPEG: fila ``i`` = candidato ``i + 1``."""
    from PIL import Image, ImageDraw, ImageFont

    width, height = cell
    columns = max((len(row) for row in rows), default=1) or 1
    sheet = Image.new("RGB", (label_w + columns * width, max(len(rows), 1) * height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=_LABEL_SIZE)
    for i, row in enumerate(rows):
        top = i * height
        draw.text((label_w // 2, top + height // 2), str(i + 1), fill="black", font=font, anchor="mm")
        for j, photo in enumerate(row[:columns]):
            image = _open_rgb(photo)
            if image is not None:
                sheet.paste(image.resize((width, height), Image.LANCZOS), (label_w + j * width, top))
        if i:
            draw.line((0, top, sheet.width, top), fill="black", width=3)
    return _jpeg(sheet, quality)
