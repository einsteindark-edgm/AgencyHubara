"""Índice de fotos del catálogo: el vector de cada foto (embedding de imagen)
para encontrar los productos más parecidos a la foto que mandó el cliente.

Vive junto al snapshot del catálogo (``<snapshot>/photo_index/``):

* ``index.json``: el modelo y las dimensiones con que se midió, el vector de
  una imagen de control y, por URL de foto, su producto, su etiqueta
  (``thumb`` = la miniatura; ``r<N>`` = la foto N del producto) y su vector;
* ``thumbs/<sha1 de la URL>.jpg``: la foto en JPEG chico, para la hoja que ve
  el verificador (sin volver a bajarla).

Reglas:

* Se llena por partes (``refresh``): solo se bajan y se miden las fotos que el
  catálogo tiene y el índice no. Una foto que no baja queda para la próxima.
* Solo cuenta lo que el catálogo tiene HOY (``nearest`` recibe los productos
  vigentes): un producto o una foto que salió nunca es candidato.
* Otro modelo, otros vectores: si cambia el modelo o las dimensiones, el índice
  guardado no sirve. El alias del proxy puede cambiar de modelo sin cambiar de
  nombre, así que ``refresh`` vuelve a medir la imagen de control y, si el
  modelo de hoy la mide distinto, rehace todo.
* Escrituras atómicas (``*.tmp`` + ``os.replace``) que suman a lo que otro
  proceso haya escrito.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import structlog

logger = structlog.get_logger()

#: Coseno mínimo entre el vector de control guardado y el de hoy para seguir
#: usando el índice (el mismo modelo repite el vector casi idéntico).
_SAME_MODEL_COSINE = 0.999
#: El lado mayor de la miniatura guardada (la celda de la hoja mide 480×270).
_THUMB_SIDE = 640
#: Tope de una foto del catálogo al bajarla.
_MAX_PHOTO_BYTES = 15 * 1024 * 1024


class _Embedder(Protocol):
    async def embed(self, image_bytes: bytes, mime_type: str) -> list[float] | None: ...


Fetch = Callable[[str], Awaitable[bytes | None]]


@dataclass(frozen=True)
class CatalogPhoto:
    """Una foto del catálogo: de qué producto es y cuál (``thumb`` o ``r<N>``)."""

    handle: str
    url: str
    tag: str


@dataclass(frozen=True)
class PhotoCandidate:
    """Un producto parecido a la foto: su mejor coseno y las fotos para
    mostrarle al verificador (la miniatura primero y la foto más parecida)."""

    handle: str
    score: float
    photos: tuple[str, ...]


@dataclass(frozen=True)
class RefreshReport:
    added: int = 0
    failed: int = 0
    redone: bool = False


def catalog_photos(products: Iterable[Any]) -> list[CatalogPhoto]:
    """Las fotos del catálogo: por producto, su miniatura y sus imágenes, cada
    URL una sola vez."""
    photos: list[CatalogPhoto] = []
    for product in products:
        handle = str(getattr(product, "handle", "") or "")
        if not handle:
            continue
        seen: set[str] = set()
        thumbnail = getattr(product, "thumbnail", None)
        if isinstance(thumbnail, str) and thumbnail:
            photos.append(CatalogPhoto(handle=handle, url=thumbnail, tag="thumb"))
            seen.add(thumbnail)
        for image in sorted(getattr(product, "images", None) or [], key=lambda i: getattr(i, "rank", 0)):
            url = getattr(image, "url", None)
            if isinstance(url, str) and url and url not in seen:
                photos.append(CatalogPhoto(handle=handle, url=url, tag=f"r{getattr(image, 'rank', 0)}"))
                seen.add(url)
    return photos


def _probe_image() -> bytes:
    """La imagen de control: siempre los mismos bytes (un degradé)."""
    from PIL import Image

    image = Image.new("RGB", (64, 64))
    image.putdata([(x * 4, y * 4, (x + y) * 2) for y in range(64) for x in range(64)])
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _thumb_name(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest() + ".jpg"


async def fetch_catalog_photo(url: str) -> bytes | None:
    """Baja una foto pública del catálogo (https); None si falla."""
    import httpx

    if not url.startswith("https://"):
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        logger.warning("catalog_photo.fetch_failed", error_type=type(exc).__name__)
        return None
    if response.status_code != 200 or len(response.content) > _MAX_PHOTO_BYTES:
        return None
    return response.content


class CatalogPhotoIndex:
    def __init__(self, root: Path, *, model: str, dimensions: int) -> None:
        self._root = Path(root)
        self._model = model
        self._dimensions = dimensions
        self._cache: tuple[float, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()

    @property
    def root(self) -> Path:
        return self._root

    # ── lectura ────────────────────────────────────────────────────────────

    def _file(self) -> Path:
        return self._root / "index.json"

    def _load(self) -> dict[str, Any]:
        """El índice guardado si es del modelo de hoy; si no, uno vacío."""
        path = self._file()
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        if self._cache is not None and self._cache[0] == mtime:
            return self._cache[1]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if (
            not isinstance(data, dict)
            or data.get("model") != self._model
            or data.get("dimensions") != self._dimensions
            or not isinstance(data.get("photos"), dict)
        ):
            data = {}
        self._cache = (mtime, data)
        return data

    def _photos(self) -> dict[str, Any]:
        return self._load().get("photos") or {}

    def missing(self, products: Iterable[Any]) -> list[CatalogPhoto]:
        """Las fotos del catálogo que el índice todavía no midió."""
        stored = self._photos()
        return [p for p in catalog_photos(products) if p.url not in stored]

    def nearest(self, vector: Sequence[float], products: Iterable[Any], *, k: int = 5) -> list[PhotoCandidate]:
        """Los ``k`` productos del catálogo de HOY más parecidos al vector."""
        stored = self._photos()
        current = catalog_photos(products)
        by_handle: dict[str, list[tuple[float, CatalogPhoto]]] = {}
        for photo in current:
            entry = stored.get(photo.url)
            if not isinstance(entry, dict) or not isinstance(entry.get("vector"), list):
                continue
            by_handle.setdefault(photo.handle, []).append((_cosine(vector, entry["vector"]), photo))
        candidates: list[PhotoCandidate] = []
        for handle, scored in by_handle.items():
            scored.sort(key=lambda item: -item[0])
            thumb = next((p for _, p in scored if p.tag == "thumb"), None)
            first = thumb or scored[0][1]
            other = next((p for _, p in scored if p.url != first.url), None)
            photos = (first.url, other.url) if other else (first.url,)
            candidates.append(PhotoCandidate(handle=handle, score=scored[0][0], photos=photos))
        candidates.sort(key=lambda c: -c.score)
        return candidates[:k]

    def photo_bytes(self, url: str) -> bytes | None:
        """La miniatura guardada de una foto del catálogo (JPEG)."""
        entry = self._photos().get(url)
        if not isinstance(entry, dict) or not isinstance(entry.get("thumb"), str):
            return None
        try:
            return (self._root / "thumbs" / entry["thumb"]).read_bytes()
        except OSError:
            return None

    # ── escritura ──────────────────────────────────────────────────────────

    async def refresh(
        self,
        products: Iterable[Any],
        *,
        embedder: _Embedder,
        fetch: Fetch = fetch_catalog_photo,
        concurrency: int = 4,
    ) -> RefreshReport:
        """Mide las fotos que faltan (y rehace todo si el modelo cambió)."""
        from src.platform.vision.images import to_jpeg

        async with self._lock:
            data = dict(self._load())
            probe = await embedder.embed(_probe_image(), "image/png")
            if probe is None:
                return RefreshReport()
            redone = False
            stored_probe = data.get("probe")
            if isinstance(stored_probe, list) and _cosine(stored_probe, probe) < _SAME_MODEL_COSINE:
                logger.info("catalog_photo_index.model_changed", model=self._model)
                data, redone = {}, True
            photos: dict[str, Any] = dict(data.get("photos") or {})
            todo = [p for p in catalog_photos(products) if p.url not in photos]
            (self._root / "thumbs").mkdir(parents=True, exist_ok=True)
            gate = asyncio.Semaphore(max(1, concurrency))

            async def measure(photo: CatalogPhoto) -> tuple[CatalogPhoto, dict[str, Any] | None]:
                async with gate:
                    raw = await fetch(photo.url)
                    thumb = to_jpeg(raw, max_side=_THUMB_SIDE) if raw else None
                    vector = await embedder.embed(raw, "image/jpeg") if thumb else None
                if vector is None or thumb is None:
                    return photo, None
                name = _thumb_name(photo.url)
                _write_atomic(self._root / "thumbs" / name, thumb)
                return photo, {"handle": photo.handle, "tag": photo.tag, "vector": vector, "thumb": name}

            results = await asyncio.gather(*(measure(p) for p in todo))
            added = 0
            for photo, entry in results:
                if entry is not None:
                    photos[photo.url] = entry
                    added += 1
            failed = len(todo) - added
            if added or redone or stored_probe is None:
                self._save(photos, probe, merge=not redone)
            logger.info("catalog_photo_index.refreshed", added=added, failed=failed, redone=redone)
            return RefreshReport(added=added, failed=failed, redone=redone)

    def _save(self, photos: dict[str, Any], probe: list[float], *, merge: bool) -> None:
        if merge:
            # Lo que otro proceso haya medido mientras tanto también queda.
            photos = {**self._photos(), **photos}
        data = {"model": self._model, "dimensions": self._dimensions, "probe": probe, "photos": photos}
        _write_atomic(self._file(), json.dumps(data).encode("utf-8"))
        self._cache = None


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
