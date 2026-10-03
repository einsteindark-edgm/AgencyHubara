"""Imágenes para comparar la foto del cliente con el catálogo.

* Las fotos del catálogo son WEBP (assets.hubara.com.co) y el embedding de
  imágenes de Gemini solo acepta PNG o JPEG: se pasan a JPEG, con el lado mayor
  acotado (una foto de portada mide 2560×1440).
* El verificador ve UNA hoja con los candidatos: una fila por producto con su
  número a la izquierda y hasta dos fotos del mismo producto (el formato con el
  que acertó 74 de 74 en la investigación del 2026-09-29).
"""
from __future__ import annotations

import io

from PIL import Image

from src.platform.vision.images import candidate_sheet, to_jpeg


def _encode(image: Image.Image, fmt: str) -> bytes:
    out = io.BytesIO()
    image.save(out, fmt)
    return out.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_a_webp_catalog_photo_becomes_a_jpeg_the_embedder_accepts() -> None:
    webp = _encode(Image.new("RGB", (2560, 1440), (200, 30, 30)), "WEBP")

    out = to_jpeg(webp, max_side=1024)

    assert out is not None
    image = _open(out)
    assert image.format == "JPEG" and image.size == (1024, 576)


def test_a_small_photo_keeps_its_size() -> None:
    out = to_jpeg(_encode(Image.new("RGB", (300, 400), (10, 10, 10)), "PNG"), max_side=1024)

    assert out is not None and _open(out).size == (300, 400)


def test_transparency_turns_white() -> None:
    png = _encode(Image.new("RGBA", (40, 40), (0, 0, 0, 0)), "PNG")

    out = to_jpeg(png, max_side=1024)

    assert out is not None
    r, g, b = _open(out).convert("RGB").getpixel((20, 20))
    assert min(r, g, b) > 245


def test_bytes_that_are_not_an_image_give_none() -> None:
    assert to_jpeg(b"hola", max_side=1024) is None


def test_the_sheet_has_one_numbered_row_per_candidate() -> None:
    red = _encode(Image.new("RGB", (100, 100), (220, 0, 0)), "PNG")
    green = _encode(Image.new("RGB", (100, 100), (0, 200, 0)), "WEBP")
    blue = _encode(Image.new("RGB", (100, 100), (0, 0, 220)), "JPEG")

    sheet = _open(candidate_sheet([[red, green], [blue]], cell=(480, 270), label_w=60))

    assert sheet.format == "JPEG" and sheet.size == (60 + 2 * 480, 2 * 270)
    rgb = sheet.convert("RGB")

    def near(pixel: tuple[int, int, int], want: tuple[int, int, int]) -> bool:
        return all(abs(a - b) < 40 for a, b in zip(pixel, want))

    assert near(rgb.getpixel((60 + 240, 135)), (220, 0, 0))
    assert near(rgb.getpixel((60 + 480 + 240, 135)), (0, 200, 0))
    assert near(rgb.getpixel((60 + 240, 270 + 135)), (0, 0, 220))
    # Sin segunda foto, la celda queda en blanco.
    assert near(rgb.getpixel((60 + 480 + 240, 270 + 135)), (255, 255, 255))
    # El número de la fila está escrito en la columna de la izquierda.
    label = rgb.crop((0, 0, 60, 270))
    assert min(low for low, _high in label.getextrema()) < 80


def test_a_candidate_photo_that_does_not_open_leaves_its_cell_blank() -> None:
    red = _encode(Image.new("RGB", (100, 100), (220, 0, 0)), "PNG")

    sheet = _open(candidate_sheet([[b"no es imagen", red]], cell=(200, 100), label_w=40)).convert("RGB")

    assert min(sheet.getpixel((40 + 100, 50))) > 245
    assert sheet.getpixel((40 + 200 + 100, 50))[0] > 180
