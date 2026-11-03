"""La imagen de la invitación: foto del pedido + cinco estrellas, en la marca.

Meta muestra el encabezado IMAGE a 1.91:1; la foto del pedido (la de «pedido
listo» o la del producto) va a la izquierda y, si no hay foto, una vela
dibujada. Siempre sale un JPEG válido (la plantilla sin foto no se envía).
"""
from __future__ import annotations

import io

from PIL import Image

from src.plugins.reviews.agent.card import CARD_SIZE, render_review_card


def _photo_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 1000), (200, 120, 60)).save(buf, "JPEG")
    return buf.getvalue()


def test_card_with_the_order_photo_is_a_jpeg_of_the_meta_ratio():
    data = render_review_card("#31", _photo_bytes())
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG"
    assert img.size == CARD_SIZE == (1200, 628)


def test_card_without_photo_still_renders():
    img = Image.open(io.BytesIO(render_review_card("#31", None)))
    assert img.size == CARD_SIZE


def test_unreadable_photo_falls_back_to_the_drawn_candle():
    img = Image.open(io.BytesIO(render_review_card("#31", b"no es una foto")))
    assert img.size == CARD_SIZE


def test_the_photo_is_on_the_left_panel():
    img = Image.open(io.BytesIO(render_review_card("#31", _photo_bytes()))).convert("RGB")
    r, g, b = img.getpixel((200, 314))
    assert (abs(r - 200), abs(g - 120), abs(b - 60)) < (25, 25, 25)


def test_a_photo_that_breaks_while_processing_still_gives_a_card(monkeypatch):
    """Premortem P11: una foto que abre pero falla al girarla o convertirla
    no deja la invitación sin enviar (antes reintentaba hasta vencer)."""
    from PIL import ImageOps

    def broken(img, *a, **k):
        raise OSError("EXIF corrupto")

    monkeypatch.setattr(ImageOps, "exif_transpose", broken)
    img = Image.open(io.BytesIO(render_review_card("#31", _photo_bytes())))
    assert img.size == CARD_SIZE


def test_a_huge_photo_is_decoded_reduced(monkeypatch):
    """Premortem I7: una foto de 48 MP no se decodifica entera (memoria de la
    caja de 4 GB): JPEG se lee reducido con `draft`."""
    buf = io.BytesIO()
    Image.new("RGB", (6000, 4000), (200, 120, 60)).save(buf, "JPEG")
    from PIL import ImageFile

    decoded = []
    original = ImageFile.ImageFile.load

    def spy(self, *a, **k):
        out = original(self, *a, **k)
        decoded.append(self.size)
        return out

    monkeypatch.setattr(ImageFile.ImageFile, "load", spy)
    data = render_review_card("#31", buf.getvalue())
    assert Image.open(io.BytesIO(data)).size == CARD_SIZE
    assert decoded and decoded[0][0] * decoded[0][1] <= 3000 * 2000
