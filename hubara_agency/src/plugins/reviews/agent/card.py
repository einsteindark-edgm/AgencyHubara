"""La imagen de la invitación a calificar (encabezado IMAGE de la plantilla).

Meta muestra el encabezado a 1.91:1. A la izquierda la foto del pedido (la de
«pedido listo» que subió el equipo, o la del producto); a la derecha el número
del pedido, la pregunta, cinco estrellas doradas y el premio, con la paleta y
las fuentes de hubara.com.co (Playfair Display itálica + Manrope, OFL, en
`assets/fonts/`). Sin foto legible, una vela dibujada: la invitación nunca se
queda sin imagen.
"""
from __future__ import annotations

import io
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

CARD_SIZE = (1200, 628)
_PANEL_W = 520
_FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

_IVORY = (249, 241, 234)
_BLUSH = (244, 226, 222)
_TERRACOTTA = (172, 64, 8)
_GOLD = (197, 160, 101)
_INK = (58, 42, 32)
_MUTED = (110, 98, 90)
_WAX = (242, 230, 216)


@lru_cache(maxsize=8)
def _font(name: str, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(_FONTS / name), size)
    if weight is not None:
        try:
            font.set_variation_by_axes([weight])
        except (OSError, ValueError):  # FreeType sin variaciones: peso por defecto
            pass
    return font


def _photo_panel(photo: bytes | None) -> Image.Image | None:
    if not photo:
        return None
    try:
        img = Image.open(io.BytesIO(photo))
        img.load()
    except Exception:  # noqa: BLE001 — foto ilegible: vela dibujada
        return None
    img = ImageOps.exif_transpose(img).convert("RGB")
    return ImageOps.fit(img, (_PANEL_W, CARD_SIZE[1]), method=Image.Resampling.LANCZOS)


def _draw_candle(draw: ImageDraw.ImageDraw) -> None:
    cx, base = _PANEL_W // 2, 500
    draw.ellipse((cx - 150, base - 18, cx + 150, base + 18), fill=(232, 214, 198))
    draw.rounded_rectangle((cx - 120, base - 250, cx + 120, base), radius=36, fill=_TERRACOTTA)
    draw.rounded_rectangle((cx - 120, base - 250, cx + 120, base - 205), radius=22, fill=_GOLD)
    draw.rounded_rectangle((cx - 95, base - 185, cx + 95, base - 45), radius=20, fill=_WAX)
    label = _font("PlayfairDisplay-Italic.ttf", 44)
    draw.text((cx, base - 115), "Hubara", font=label, fill=_TERRACOTTA, anchor="mm")
    draw.rounded_rectangle((cx - 5, base - 300, cx + 5, base - 250), radius=4, fill=_INK)
    draw.ellipse((cx - 26, base - 380, cx + 26, base - 290), fill=(233, 162, 59))
    draw.ellipse((cx - 11, base - 345, cx + 11, base - 300), fill=(255, 241, 201))


def _star(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float) -> None:
    points = []
    for i in range(10):
        radius = r if i % 2 == 0 else r * 0.45
        angle = -math.pi / 2 + i * math.pi / 5
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(points, fill=_GOLD)


def render_review_card(order_reference: str, photo: bytes | None) -> bytes:
    """JPEG 1200×628 para el encabezado de la invitación del pedido."""
    card = Image.new("RGB", CARD_SIZE, _IVORY)
    draw = ImageDraw.Draw(card)
    draw.rectangle((0, 0, _PANEL_W, CARD_SIZE[1]), fill=_BLUSH)
    panel = _photo_panel(photo)
    if panel is not None:
        card.paste(panel, (0, 0))
    else:
        _draw_candle(draw)

    x = _PANEL_W + 64
    eyebrow = _font("Manrope.ttf", 30, 700)
    title = _font("PlayfairDisplay-Italic.ttf", 70, 500)
    body = _font("Manrope.ttf", 32, 600)
    small = _font("Manrope.ttf", 26, 500)
    draw.text((x, 96), f"PEDIDO {order_reference}".upper(), font=eyebrow, fill=_TERRACOTTA)
    draw.text((x, 150), "¿Qué tal tu", font=title, fill=_INK)
    draw.text((x, 232), "nueva vela?", font=title, fill=_INK)
    for i in range(5):
        _star(draw, x + 38 + i * 86, 382, 36)
    draw.text((x, 452), "Califícala y gana 5 %", font=body, fill=_INK)
    draw.text((x, 532), "Hecha a mano en Bogotá", font=small, fill=_MUTED)

    out = io.BytesIO()
    card.save(out, "JPEG", quality=88, optimize=True)
    return out.getvalue()
