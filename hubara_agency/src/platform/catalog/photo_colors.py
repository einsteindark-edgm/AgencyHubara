"""El color de la vela en cada foto del catálogo — leído UNA vez y guardado.

Caso del 2026-10-09: el bot mandó la foto de Encanto Silvestre (una ardilla
café), en el selector salieron solo gris, amarillo y verde, y el cliente citó
la foto: «¿no viene en este color?». Nadie sabía de qué color era la vela.

`color_of_photo` le pregunta al lector de visión (`PhotoColorPort`, lista
cerrada = los colores del producto) la primera vez que se necesita el color de
una foto y lo deja junto al snapshot del catálogo
(`<snapshot>/photo_colors/<sha1 de la URL>.json`, un archivo por foto: los
workers escriben sin pisarse). La segunda vez sale del disco. Si la paleta del
producto cambia, se vuelve a preguntar. Una foto que no permite decirlo
(varias velas de colores distintos) se recuerda como `None`; una lectura
fallida no se recuerda (se intenta la próxima vez).
"""
from __future__ import annotations

import hashlib
import json
import time
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import structlog

from src.platform.state import atomic_write_json

logger = structlog.get_logger()

#: La foto que ve el lector: JPEG con el lado mayor ≤ esto (menos tokens).
_MAX_SIDE = 768


@dataclass(frozen=True)
class PhotoColorRecord:
    url: str
    palette: tuple[str, ...]
    #: Uno de la paleta, o None: la foto no permite decirlo.
    color: str | None
    model: str = ""
    at_ms: int = 0


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold().strip()


def _same_palette(a: Sequence[str], b: Sequence[str]) -> bool:
    return sorted(map(_fold, a)) == sorted(map(_fold, b))


class VaultPhotoColorStore:
    """`<root>/<sha1 de la URL>.json` por foto (escritura atómica)."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _path(self, url: str) -> Path:
        return self._root / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")

    def get(self, url: str) -> PhotoColorRecord | None:
        """Lo guardado de esa foto; None si no hay o el archivo está roto
        (es un caché: se vuelve a preguntar)."""
        try:
            data = json.loads(self._path(url).read_text(encoding="utf-8"))
            return PhotoColorRecord(
                url=str(data["url"]),
                palette=tuple(str(c) for c in data.get("palette") or ()),
                color=data["color"] if isinstance(data.get("color"), str) else None,
                model=str(data.get("model") or ""),
                at_ms=int(data.get("at_ms") or 0),
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("photo_color.unreadable", error_type=type(exc).__name__)
            return None

    def put(self, record: PhotoColorRecord) -> None:
        path = self._path(record.url)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, asdict(record) | {"palette": list(record.palette)})


async def color_of_photo(
    url: str,
    *,
    title: str,
    palette: Sequence[str],
    store: Any,
    reader: Any,
    fetch: Callable[[str], Awaitable[bytes | None]] | None = None,
) -> str | None:
    """El color de la vela de la foto `url` (uno de `palette`), o None.

    Lee lo guardado si la paleta es la misma; si no, baja la foto, se la
    muestra al lector y guarda lo que dijo (también `None`). Nunca lanza."""
    colors = tuple(c for c in palette if isinstance(c, str) and c.strip())
    if not url or not colors:
        return None
    saved = store.get(url)
    if saved is not None and _same_palette(saved.palette, colors):
        return saved.color
    if fetch is None:
        from src.platform.catalog.photo_index import fetch_catalog_photo as fetch
    try:
        raw = await fetch(url)
    except Exception as exc:  # noqa: BLE001 — el color es un extra: nunca tumba al que llama
        logger.warning("photo_color.fetch_failed", error_type=type(exc).__name__)
        return None
    if not raw:
        return None
    from src.platform.vision.images import to_jpeg

    photo = to_jpeg(raw, max_side=_MAX_SIDE)
    if photo is None:
        return None
    pick = await reader.pick_color(photo, "image/jpeg", title=title, palette=colors)
    if not pick.ok:
        return None
    logger.info("photo_color.read", color=pick.color, cost_usd=pick.cost_usd, latency_ms=pick.latency_ms)
    try:
        store.put(PhotoColorRecord(url=url, palette=colors, color=pick.color,
                                   model=getattr(reader, "name", ""), at_ms=int(time.time() * 1000)))
    except OSError as exc:
        logger.warning("photo_color.unsaved", error_type=type(exc).__name__)
    return pick.color


__all__ = ["PhotoColorRecord", "VaultPhotoColorStore", "color_of_photo"]
