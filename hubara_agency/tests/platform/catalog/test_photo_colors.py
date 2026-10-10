"""El color de la vela en cada foto del catálogo, leído UNA vez y guardado.

Caso del 2026-10-09: el cliente citó la foto de Encanto Silvestre («¿no viene
en este color?») y nadie sabía que la ardilla de la foto es café. El color de
cada foto se le pregunta al lector de visión la primera vez que se necesita y
queda junto al catálogo (`<snapshot>/photo_colors/`): la segunda vez sale del
disco. Si cambia la paleta del producto, se vuelve a preguntar.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from src.platform.catalog.photo_colors import VaultPhotoColorStore, color_of_photo
from src.platform.vision.photo_color import ColorPick, FakePhotoColorAdapter

URL = "https://assets.hubara.com.co/ardilla-01ABC.webp"
PALETTE = ("gris", "Amarillo", "verde", "Café")


def _png(rgb: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), rgb).save(out, "PNG")
    return out.getvalue()


PHOTO = _png((120, 80, 50))


class _Fetch:
    def __init__(self, data: bytes | None = PHOTO) -> None:
        self.data = data
        self.urls: list[str] = []

    async def __call__(self, url: str) -> bytes | None:
        self.urls.append(url)
        return self.data


class _Reader(FakePhotoColorAdapter):
    """Contesta café para cualquier foto (los bytes cambian al pasar a JPEG)."""

    def __init__(self, answer: ColorPick | None = None) -> None:
        super().__init__()
        self.answer = answer or ColorPick(ok=True, color="Café")

    async def pick_color(self, photo, mime_type, *, title, palette):
        self.calls.append(title)
        return self.answer


@pytest.mark.asyncio
async def test_the_color_is_asked_once_and_then_read_from_disk(tmp_path: Path) -> None:
    store, reader, fetch = VaultPhotoColorStore(tmp_path), _Reader(), _Fetch()

    first = await color_of_photo(URL, title="Encanto Silvestre", palette=PALETTE, store=store, reader=reader, fetch=fetch)
    second = await color_of_photo(URL, title="Encanto Silvestre", palette=PALETTE, store=store, reader=reader, fetch=fetch)

    assert first == second == "Café"
    assert reader.calls == ["Encanto Silvestre"]
    assert fetch.urls == [URL]
    assert store.get(URL).color == "Café"


@pytest.mark.asyncio
async def test_a_new_palette_asks_again(tmp_path: Path) -> None:
    store, reader = VaultPhotoColorStore(tmp_path), _Reader()
    await color_of_photo(URL, title="X", palette=PALETTE, store=store, reader=reader, fetch=_Fetch())

    await color_of_photo(URL, title="X", palette=(*PALETTE, "Negro"), store=store, reader=reader, fetch=_Fetch())

    assert len(reader.calls) == 2


@pytest.mark.asyncio
async def test_a_photo_that_does_not_tell_is_remembered_as_unknown(tmp_path: Path) -> None:
    store, reader = VaultPhotoColorStore(tmp_path), _Reader(ColorPick(ok=True, color=None))

    assert await color_of_photo(URL, title="Trilogía", palette=PALETTE, store=store, reader=reader, fetch=_Fetch()) is None
    assert await color_of_photo(URL, title="Trilogía", palette=PALETTE, store=store, reader=reader, fetch=_Fetch()) is None

    assert len(reader.calls) == 1


@pytest.mark.asyncio
async def test_a_failed_reading_is_not_remembered(tmp_path: Path) -> None:
    store, reader = VaultPhotoColorStore(tmp_path), _Reader(ColorPick(ok=False, error="provider_error"))

    assert await color_of_photo(URL, title="X", palette=PALETTE, store=store, reader=reader, fetch=_Fetch()) is None

    assert store.get(URL) is None


@pytest.mark.asyncio
async def test_a_photo_that_cannot_be_downloaded_is_not_asked(tmp_path: Path) -> None:
    store, reader = VaultPhotoColorStore(tmp_path), _Reader()

    assert await color_of_photo(URL, title="X", palette=PALETTE, store=store, reader=reader, fetch=_Fetch(None)) is None

    assert reader.calls == [] and store.get(URL) is None


@pytest.mark.asyncio
async def test_a_product_without_colors_has_nothing_to_ask(tmp_path: Path) -> None:
    fetch, reader = _Fetch(), _Reader()

    assert await color_of_photo(URL, title="X", palette=(), store=VaultPhotoColorStore(tmp_path), reader=reader,
                                fetch=fetch) is None

    assert fetch.urls == [] and reader.calls == []


def test_a_broken_record_is_read_as_missing(tmp_path: Path) -> None:
    from src.platform.catalog.photo_colors import PhotoColorRecord

    store = VaultPhotoColorStore(tmp_path)
    store.put(PhotoColorRecord(url=URL, palette=PALETTE, color="Café"))
    [path] = list(tmp_path.rglob("*.json"))
    path.write_text("{roto", encoding="utf-8")

    # Un registro roto se vuelve a preguntar (es un caché, no un dato del negocio).
    assert store.get(URL) is None
