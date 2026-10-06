"""Índice de fotos del catálogo: el vector de cada foto, para encontrar los
productos más parecidos a la foto que mandó el cliente.

* Vive junto al snapshot del catálogo (``<snapshot>/photo_index/``) y se llena
  por partes: solo se bajan y se miden las fotos que faltan.
* Solo cuenta lo que el catálogo tiene HOY: una foto o un producto que ya no
  está no aparece como candidato.
* Si cambia el modelo de embeddings, los vectores viejos no sirven: el índice
  se rehace (un modelo nuevo mide distinto la misma foto).
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from src.platform.catalog.dtos import CatalogImageDTO, CatalogProductDTO
from src.platform.catalog.photo_index import CatalogPhotoIndex, catalog_photos
from src.platform.vision.embeddings import FakeImageEmbeddingAdapter


def _jpeg(color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (80, 60), color).save(out, "WEBP")
    return out.getvalue()


PHOTOS = {
    "https://assets.test/luz-serena.webp": _jpeg((230, 220, 200)),
    "https://assets.test/luz-serena-2.webp": _jpeg((180, 200, 230)),
    "https://assets.test/sacrificio.webp": _jpeg((120, 120, 120)),
    "https://assets.test/sacrificio-2.webp": _jpeg((140, 110, 60)),
    "https://assets.test/gorrion.webp": _jpeg((190, 160, 220)),
}


def _product(handle: str, thumb: str | None, *images: str) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}",
        handle=handle,
        title=handle.replace("-", " ").title(),
        status="published",
        thumbnail=thumb,
        images=[CatalogImageDTO(url=u, rank=i) for i, u in enumerate(images)],
    )


CATALOG = [
    _product("luz-serena", "https://assets.test/luz-serena.webp",
             "https://assets.test/luz-serena.webp", "https://assets.test/luz-serena-2.webp"),
    _product("sacrificio-de-amor", "https://assets.test/sacrificio.webp", "https://assets.test/sacrificio-2.webp"),
    _product("velon-gorrion", None, "https://assets.test/gorrion.webp"),
]


def _fetch(photos: dict[str, bytes], seen: list[str] | None = None):
    async def fetch(url: str) -> bytes | None:
        if seen is not None:
            seen.append(url)
        return photos.get(url)

    return fetch


def _index(tmp_path: Path, embedder: FakeImageEmbeddingAdapter | None = None) -> CatalogPhotoIndex:
    embedder = embedder or FakeImageEmbeddingAdapter(dimensions=16)
    return CatalogPhotoIndex(tmp_path / "photo_index", model=embedder.model, dimensions=embedder.dimensions)


def test_the_photos_of_the_catalog_are_its_thumbnail_and_its_images_once_each() -> None:
    photos = catalog_photos(CATALOG)

    assert [(p.handle, p.tag) for p in photos] == [
        ("luz-serena", "thumb"),
        ("luz-serena", "r1"),
        ("sacrificio-de-amor", "thumb"),
        ("sacrificio-de-amor", "r0"),
        ("velon-gorrion", "r0"),
    ]


async def test_a_refresh_measures_every_photo_and_keeps_a_thumbnail(tmp_path: Path) -> None:
    embedder = FakeImageEmbeddingAdapter(dimensions=16)
    index = _index(tmp_path, embedder)

    report = await index.refresh(CATALOG, embedder=embedder, fetch=_fetch(PHOTOS))

    assert report.added == 5 and report.failed == 0
    assert index.missing(CATALOG) == []
    stored = json.loads((tmp_path / "photo_index" / "index.json").read_text())
    assert stored["model"] == "fake" and stored["dimensions"] == 16 and len(stored["photos"]) == 5
    thumb = index.photo_bytes("https://assets.test/sacrificio.webp")
    assert thumb is not None and Image.open(io.BytesIO(thumb)).format == "JPEG"


async def test_only_the_missing_photos_are_downloaded_again(tmp_path: Path) -> None:
    embedder = FakeImageEmbeddingAdapter(dimensions=16)
    index = _index(tmp_path, embedder)
    broken = {u: b for u, b in PHOTOS.items() if "gorrion" not in u}
    first = await index.refresh(CATALOG, embedder=embedder, fetch=_fetch(broken))
    seen: list[str] = []

    second = await _index(tmp_path, embedder).refresh(CATALOG, embedder=embedder, fetch=_fetch(PHOTOS, seen))

    assert (first.added, first.failed) == (4, 1)
    assert seen == ["https://assets.test/gorrion.webp"] and second.added == 1


async def test_the_nearest_products_come_with_their_thumbnail_first(tmp_path: Path) -> None:
    embedder = FakeImageEmbeddingAdapter(dimensions=16)
    index = _index(tmp_path, embedder)
    await index.refresh(CATALOG, embedder=embedder, fetch=_fetch(PHOTOS))
    # La foto del cliente ES la segunda foto de la Luz Serena.
    vector = await embedder.embed(PHOTOS["https://assets.test/luz-serena-2.webp"], "image/webp")
    assert vector is not None

    candidates = index.nearest(vector, CATALOG, k=2)

    assert [c.handle for c in candidates][0] == "luz-serena" and len(candidates) == 2
    assert candidates[0].score == pytest.approx(1.0)
    assert candidates[0].photos == ("https://assets.test/luz-serena.webp", "https://assets.test/luz-serena-2.webp")


async def test_what_the_catalog_no_longer_has_is_never_a_candidate(tmp_path: Path) -> None:
    embedder = FakeImageEmbeddingAdapter(dimensions=16)
    index = _index(tmp_path, embedder)
    await index.refresh(CATALOG, embedder=embedder, fetch=_fetch(PHOTOS))
    vector = await embedder.embed(PHOTOS["https://assets.test/luz-serena-2.webp"], "image/webp")
    assert vector is not None
    # La Luz Serena salió del catálogo y el Sacrificio cambió de fotos.
    today = [_product("sacrificio-de-amor", "https://assets.test/sacrificio.webp"), CATALOG[2]]

    candidates = index.nearest(vector, today, k=5)

    assert {c.handle for c in candidates} == {"sacrificio-de-amor", "velon-gorrion"}
    assert all("sacrificio-2" not in url for c in candidates for url in c.photos)


async def test_another_embedding_model_starts_the_index_over(tmp_path: Path) -> None:
    embedder = FakeImageEmbeddingAdapter(dimensions=16)
    await _index(tmp_path, embedder).refresh(CATALOG, embedder=embedder, fetch=_fetch(PHOTOS))

    other = CatalogPhotoIndex(tmp_path / "photo_index", model="otro-modelo", dimensions=16)

    assert len(other.missing(CATALOG)) == 5 and other.nearest([1.0] * 16, CATALOG) == []


async def test_without_an_index_there_are_no_candidates(tmp_path: Path) -> None:
    assert _index(tmp_path).nearest([1.0] * 16, CATALOG) == []


class _NewModel(FakeImageEmbeddingAdapter):
    """El mismo alias con otro modelo detrás: mide distinto la misma foto."""

    async def embed(self, image_bytes: bytes, mime_type: str) -> list[float] | None:
        return await super().embed(image_bytes + b"otro-modelo", mime_type)


async def test_a_new_model_behind_the_same_alias_is_noticed_and_the_index_redone(tmp_path: Path) -> None:
    """El alias del proxy (`gemini-embedding`) puede cambiar de modelo sin
    cambiar de nombre: el índice guarda el vector de una imagen de control y,
    si el modelo de hoy la mide distinto, vuelve a medir todo."""
    old = FakeImageEmbeddingAdapter(dimensions=16)
    await _index(tmp_path, old).refresh(CATALOG, embedder=old, fetch=_fetch(PHOTOS))
    new = _NewModel(dimensions=16)
    index = _index(tmp_path, new)

    report = await index.refresh(CATALOG, embedder=new, fetch=_fetch(PHOTOS))

    assert report.added == 5 and report.redone is True
    vector = await new.embed(PHOTOS["https://assets.test/gorrion.webp"], "image/webp")
    assert vector is not None
    assert index.nearest(vector, CATALOG, k=1)[0].score == pytest.approx(1.0)
