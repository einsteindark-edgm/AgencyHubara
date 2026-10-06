"""El laboratorio vuelve a leer las fotos del cliente con la visión de hoy.

Hasta el 2026-09-29 el sandbox reusaba la descripción que escribió la visión
de PRODUCCIÓN en su momento, así que un cambio en la visión (el texto que se lee
en la foto, la identificación contra el catálogo) no se podía medir. El banco
trae ahora las fotos de producto (`launch/bench_export.py`) y cada una pasa por
lo MISMO que en el ingest de producción (`_describe_image_and_reenter`): la
visión, el identificador (`sales/use_cases/photo_product`), el texto con que la
foto entra a la conversación y la nota del turno.

* Solo cambia el segmento de la foto: lo que el ingest agregó alrededor (el
  banner del anuncio, el texto que el cliente puso en la foto) queda igual.
  Una foto que producción no pudo ver («no pude ver bien») se lee ahora.
* Sin la foto en el banco, o si la visión falla, queda el texto de producción.
* Lo que la visión dijo de cada foto se guarda en el banco (``cache_dir``): la
  visión corre una vez por foto y todos los brazos ven la misma lectura.
* Candado del sandbox (plan §3.5): un caso no abre conexiones fuera de la caja
  ni escribe fuera de su carpeta. La visión y el índice de fotos corren al
  PREPARAR la corrida (``read_bench_photos``, en ``lab_run_prepare``); dentro
  del caso el paso va sin visión (``vision=None``) y solo lee lo que quedó.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

#: El segmento de la foto en el texto con que entró: la descripción de la
#: visión o el aviso de que no se pudo ver.
_PHOTO_SEGMENT = re.compile(r"\[el cliente envió una foto: [^\]]*\]|\[el cliente envió una imagen que no pude ver bien\]")


@dataclass(frozen=True)
class PhotoReread:
    """Lo que la visión de hoy dijo de una foto del banco."""

    annotation: str  # el segmento nuevo de la foto (sin el texto del cliente)
    note: str | None  # la nota del turno si es un producto nuestro
    product: dict[str, Any] | None  # {"handle", "how"} o None
    description: str
    trace: dict[str, Any] | None = None

    def apply(self, text: str) -> str:
        """El texto con la foto leída de nuevo (el resto queda igual)."""
        return _PHOTO_SEGMENT.sub(lambda _m: self.annotation, text, count=1)


def _plain_name(name: Any) -> bool:
    return isinstance(name, str) and bool(name) and Path(name).name == name and not name.startswith(".")


class LabPhotoStep:
    def __init__(self, *, media_dir: Path, vision: Any, identifier: Any, cache_dir: Path | None = None) -> None:
        self._media_dir = Path(media_dir)
        self._vision = vision
        self._identifier = identifier
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None

    def _cache_file(self, name: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{self._media_dir.parent.name}__{name}.json"

    def _cached(self, name: str) -> PhotoReread | None:
        path = self._cache_file(name)
        if path is None:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return PhotoReread(**data)
        except (OSError, ValueError, TypeError):
            return None

    def _remember(self, name: str, reread: PhotoReread) -> None:
        path = self._cache_file(name)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(reread), ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    async def reread(self, message: Mapping[str, Any]) -> PhotoReread | None:
        """La foto del mensaje leída con la visión de hoy, o None (queda el
        texto de producción)."""
        name = message.get("image")
        if not _plain_name(name):
            return None
        path = self._media_dir / name
        if not path.is_file():
            return None
        cached = self._cached(name)
        if cached is not None or self._vision is None or self._identifier is None:
            return cached
        data = path.read_bytes()
        # El índice de fotos del catálogo del banco, completo antes de buscar
        # (en producción se completa en segundo plano).
        refresh = getattr(self._identifier, "refresh_index", None)
        if refresh is not None:
            await refresh()
        result = await self._vision.describe_image(data, "image/jpeg")
        if not result.ok or result.is_payment_receipt:
            logger.info("lab_photo.vision_unavailable", error=result.error)
            return None

        # El texto y la nota de producción (import local: el laboratorio corre
        # el código de ventas sin acoplarse a él al importarse, R-DIP #10).
        from src.plugins.chats.agent.sales.use_cases.photo_product import (
            build_photo_product_note,
            photo_reentry_text,
        )

        async def image() -> tuple[bytes, str]:
            return data, "image/jpeg"

        identification = await self._identifier.identify(result, image)
        product = identification.product
        reread = PhotoReread(
            annotation=photo_reentry_text(result.description, product),
            note=build_photo_product_note(product, result.description) if product is not None else None,
            product={"handle": product.handle, "how": product.how} if product is not None else None,
            description=result.description,
            trace=identification.trace or None,
        )
        self._remember(name, reread)
        return reread


# ── Las fotos de turnos anteriores (laboratorio 4567 t13) ───────────────────
# El turno se simula sobre el historial de producción: las fotos de turnos
# anteriores traen la descripción de la visión de ENTONCES, sin el producto, y
# el metadata del banco trae todas las fotos del día (también las que llegaron
# después del turno). El ingest de hoy las habría guardado reconocidas
# (`recent_image_descriptions` con el producto y el episodio) y el historial
# las mostraría nombrando el producto: el sandbox arma el turno así.

_TITLE_RE = re.compile(r"nuestro producto «([^»]+)»")


def bench_reads(bench_dir: Path, session_id: str) -> Callable[[Any], PhotoReread | None]:
    """La lectura de hoy de una foto del banco, por su archivo (None si no hay)."""
    cache = Path(bench_dir) / "photo_reads"

    def read(filename: Any) -> PhotoReread | None:
        if not _plain_name(filename):
            return None
        try:
            data = json.loads((cache / f"{session_id}__{filename}.json").read_text(encoding="utf-8"))
            return PhotoReread(**data)
        except (OSError, ValueError, TypeError):
            return None

    return read


def stored_photo(entry: Mapping[str, Any], reread: PhotoReread | None, *, episode_id: Any) -> dict[str, Any]:
    """La foto como la guarda el ingest de hoy en `recent_image_descriptions`:
    con su episodio y, si se reconoció, el producto con su nombre."""
    out: dict[str, Any] = {**entry}
    if reread is not None:
        out["description"] = reread.description
    if episode_id:
        out["episode_id"] = episode_id
    product = dict((reread.product if reread is not None else None) or {})
    title = product.get("title") or (m.group(1) if reread is not None and (m := _TITLE_RE.search(reread.annotation)) else None)
    if product.get("handle") and title:
        out["product"] = {"handle": product["handle"], "how": product.get("how"), "title": title}
    return out


def _earlier_photos(
    metadata: Mapping[str, Any], case: Mapping[str, Any], reads: Callable[[Any], PhotoReread | None]
) -> list[tuple[dict[str, Any], dict[str, Any], PhotoReread | None]]:
    """(foto guardada, índice, lectura de hoy) de las fotos que llegaron ANTES
    de la ráfaga del caso. Las de la ráfaga las guarda el ingest del sandbox
    al leerlas; una foto sin fecha no se puede ubicar y no entra."""
    at = int(case.get("at_ms") or 0)
    in_burst = {m.get("image") for m in case.get("burst") or [] if isinstance(m, Mapping)}
    media = {m.get("media_id"): m for m in metadata.get("media_index") or [] if isinstance(m, Mapping)}
    out = []
    for entry in metadata.get("recent_image_descriptions") or []:
        info = media.get(entry.get("media_id")) if isinstance(entry, Mapping) else None
        created = (info or {}).get("created_at_ms")
        if not isinstance(created, (int, float)) or created >= at or info.get("filename") in in_burst:
            continue
        out.append((dict(entry), dict(info), reads(info.get("filename"))))
    return out


def photos_as_of(
    metadata: Mapping[str, Any], case: Mapping[str, Any], reads: Callable[[Any], PhotoReread | None]
) -> list[dict[str, Any]]:
    """`recent_image_descriptions` como lo habría dejado el ingest de hoy al
    empezar la ráfaga del caso."""
    return [stored_photo(entry, reread, episode_id=info.get("episode_id"))
            for entry, info, reread in _earlier_photos(metadata, case, reads)]


def photo_rewrites(
    metadata: Mapping[str, Any], case: Mapping[str, Any], reads: Callable[[Any], PhotoReread | None]
) -> dict[str, str]:
    """El segmento con que cada foto anterior entró en producción → el de hoy."""
    return {
        f"[el cliente envió una foto: {entry.get('description')}]": reread.annotation
        for entry, _info, reread in _earlier_photos(metadata, case, reads)
        if reread is not None and entry.get("description")
    }


def rewrite_photos(text: Any, rewrites: Mapping[str, str]) -> Any:
    if not isinstance(text, str) or not rewrites:
        return text
    for old, new in rewrites.items():
        text = text.replace(old, new)
    return text


async def read_bench_photos(bench_dir: Path, *, vision: Any, identifier: Any) -> int:
    """Lee con la visión de hoy cada foto de producto del banco que todavía no
    tiene lectura (``<banco>/photo_reads``). Devuelve cuántas leyó. Una foto que
    falla queda sin lectura: su caso usa el texto de producción."""
    vault = Path(bench_dir) / "vault"
    cache = Path(bench_dir) / "photo_reads"
    read = 0
    for media in sorted(vault.glob("*/media")):
        step = LabPhotoStep(media_dir=media, vision=vision, identifier=identifier, cache_dir=cache)
        for photo in sorted(p for p in media.iterdir() if p.is_file()):
            if step._cached(photo.name) is not None:
                continue
            try:
                if await step.reread({"image": photo.name}) is not None:
                    read += 1
            except Exception as exc:  # noqa: BLE001 — una foto no frena la corrida
                logger.warning("lab_photo.read_failed", error_type=type(exc).__name__)
    return read
