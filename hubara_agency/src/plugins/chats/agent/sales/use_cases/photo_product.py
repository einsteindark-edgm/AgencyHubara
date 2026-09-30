"""La foto del cliente contra el catálogo: ¿qué producto nuestro es?

Caso del 2026-09-28 (anuncio de Halloween) y laboratorio caso-fotos-0929: el
cliente mandó capturas de NUESTRO catálogo de WhatsApp (nombre, precio y a
veces la URL en grande) y fotos sin texto; el bot negó productos que sí
tenemos o mostró el equivocado (Sagrado Rostro por Sacrificio de Amor). La
visión describía la forma y el LLM emparejaba texto contra texto.

Tres pasos, del más barato y seguro al más caro (investigación 2026-09-29/30):

1. **Texto visible** (la visión lo copia por campo): el código (SKU), el
   enlace de NUESTRA tienda (``/products/<handle>``, aunque venga cortado) o el
   nombre exacto o casi exacto (≥ 0,90 y 0,08 por encima del segundo). En las
   capturas medidas: 5 de 5, y rechaza la captura de otra tienda y el nombre
   parecido. El precio nunca decide; si se lee uno que no es el nuestro, puede
   ser otra tienda con el mismo nombre y la imagen confirma.
2. **Imagen**, solo en fotos de producto y si el texto no decidió: los 5
   productos más parecidos por embedding (índice de fotos del catálogo) y el
   verificador («el mismo diseño o ninguno»): 74 de 74, ~2 s. Con tiempo
   máximo: si no alcanza, la foto queda sin identificar.
3. **Nada**: sin identificación; el turno sigue con la descripción, como hoy.

Lo que sale de acá: el texto que reentra a la conversación (queda en el
historial y en el dashboard) y la nota del turno para el LLM (formato de
metadata, como la del producto visto en la web). Tuteo colombiano (REGLA #1
IDENTITY.md; guard ``test_no_voseo_in_agent_strings``).
"""
from __future__ import annotations

import asyncio
import difflib
import re
import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from src.sdk.catalogkit import CatalogPhotoIndex, CatalogPort
    from src.sdk.connectorkit import ImageEmbeddingPort, PhotoMatchPort, VisibleText, VisionResult

logger = structlog.get_logger()

HOW_CODE = "codigo"
HOW_LINK = "enlace"
HOW_NAME = "nombre"
HOW_IMAGE = "imagen"

_SKU_RE = re.compile(r"(?<![A-Z0-9-])HUB-[A-Z0-9]+(?:-[A-Z0-9]+)*(?![A-Z0-9])", re.IGNORECASE)
_PRODUCT_PATH_RE = re.compile(r"/products/([a-z0-9-]+)", re.IGNORECASE)
#: El enlace tiene que ser de NUESTRA tienda: la captura de otra tienda puede
#: traer un handle igual a uno nuestro («/products/angel»).
_OUR_STORE = "hubara"
#: Un enlace cortado («luz-de-bel...») vale si es el comienzo de un solo handle.
_MIN_LINK_PREFIX = 6
_FUZZY_MIN = 0.90
_FUZZY_MARGIN = 0.08
#: Un precio que se lea por debajo de esto no se compara (¿miles? ¿otra moneda?).
_MIN_PRICE = 1000
_PRICE_RE = re.compile(r"(\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{2}(?!\d))?")
#: Candidatos que ve el verificador (con los que se midió).
_CANDIDATES = 5
#: Tope de la búsqueda por imagen (embedding ~1,2 s + verificador: mediana
#: ~2 s, máximo medido 5,7 s). La foto espera esto antes de entrar al chat: si
#: el cliente escribe algo justo después de mandarla, su texto puede formar
#: turno aparte (la ráfaga cierra tras 1,5 s de silencio).
IMAGE_BUDGET_S = 6.0
#: Tope de productos al leer el catálogo (el catálogo entero).
_CATALOG_LIMIT = 500
_CATALOG_TIMEOUT_S = 3.0

ImageLoader = Callable[[], Awaitable["tuple[bytes, str] | None"]]


@dataclass(frozen=True)
class PhotoProduct:
    """El producto nuestro que es la foto, y cómo se supo (``how``: código,
    enlace, nombre o imagen). ``seen``: lo que se leyó (solo para la traza)."""

    handle: str
    title: str
    how: str
    seen: str | None = None


@dataclass(frozen=True)
class PhotoIdentification:
    product: PhotoProduct | None
    trace: dict[str, Any] = field(default_factory=dict)


# ── 1. El texto que se lee en la foto ───────────────────────────────────────


def _norm(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _strings(visible: VisibleText) -> list[str]:
    return [s for s in (visible.sku, visible.product_name, visible.url, *visible.other) if s]


def _by_code(visible: VisibleText, products: Sequence[Any]) -> PhotoProduct | None:
    by_sku = {
        str(variant.sku).upper(): product
        for product in products
        for variant in (getattr(product, "variants", None) or [])
        if getattr(variant, "sku", None)
    }
    for text in _strings(visible):
        for token in _SKU_RE.findall(text):
            product = by_sku.get(token.upper())
            if product is not None:
                return PhotoProduct(handle=product.handle, title=product.title, how=HOW_CODE, seen=token.upper())
    return None


def _by_link(visible: VisibleText, products: Sequence[Any]) -> PhotoProduct | None:
    for text in (s for s in (visible.url, *visible.other) if s):
        if _OUR_STORE not in text.lower():
            continue
        for slug in _PRODUCT_PATH_RE.findall(text):
            slug = slug.lower().strip("-")
            exact = [p for p in products if p.handle == slug]
            prefix = [p for p in products if p.handle.startswith(slug)] if len(slug) >= _MIN_LINK_PREFIX else []
            found = exact or (prefix if len(prefix) == 1 else [])
            if found:
                return PhotoProduct(handle=found[0].handle, title=found[0].title, how=HOW_LINK, seen=text)
    return None


def _by_name(visible: VisibleText, products: Sequence[Any]) -> PhotoProduct | None:
    name = _norm(visible.product_name)
    if not name or not products:
        return None
    for product in products:
        if name in (_norm(product.title), _norm(product.handle)):
            return PhotoProduct(handle=product.handle, title=product.title, how=HOW_NAME, seen=visible.product_name)
    scored = sorted(
        ((difflib.SequenceMatcher(None, name, _norm(p.title)).ratio(), i) for i, p in enumerate(products)),
        reverse=True,
    )
    best, second = scored[0][0], (scored[1][0] if len(scored) > 1 else 0.0)
    if best >= _FUZZY_MIN and best - second >= _FUZZY_MARGIN:
        product = products[scored[0][1]]
        return PhotoProduct(handle=product.handle, title=product.title, how=HOW_NAME, seen=visible.product_name)
    return None


def match_visible_text(visible: VisibleText | None, products: Iterable[Any]) -> PhotoProduct | None:
    """El producto que dice el texto de la foto: código → enlace de nuestra
    tienda → nombre. None si el texto no alcanza para decidir."""
    if visible is None:
        return None
    products = [p for p in products if getattr(p, "handle", None)]
    return _by_code(visible, products) or _by_link(visible, products) or _by_name(visible, products)


def _price(text: str | None) -> int | None:
    match = _PRICE_RE.search(text or "")
    if match is None:
        return None
    value = int(re.sub(r"[.,]", "", match.group(1)))
    return value if value >= _MIN_PRICE else None


def _price_is_not_ours(visible: VisibleText | None, product: Any) -> bool:
    """Se lee un precio y no es ninguno de los del producto."""
    seen = _price(visible.price if visible else None)
    if seen is None:
        return False
    ours = set()
    for variant in getattr(product, "variants", None) or []:
        for price in getattr(variant, "prices", None) or []:
            try:
                ours.add(int(float(price.amount)))
            except (TypeError, ValueError):
                continue
    return bool(ours) and seen not in ours


# ── 2. La imagen ───────────────────────────────────────────────────────────


class PhotoIdentifier:
    """Identifica la foto del cliente: texto primero, imagen después.

    Nunca lanza: sin catálogo, sin índice, sin vector o sin respuesta del
    verificador a tiempo, la foto queda sin identificar (y el turno sigue
    como hoy). ``refresh_index`` mantiene el índice de fotos al día (lo llama
    el ingest en segundo plano).
    """

    def __init__(
        self,
        *,
        catalog: CatalogPort,
        index: CatalogPhotoIndex | None,
        embedder: ImageEmbeddingPort | None,
        matcher: PhotoMatchPort | None,
        image_budget_s: float = IMAGE_BUDGET_S,
    ) -> None:
        self._catalog = catalog
        self._index = index
        self._embedder = embedder
        self._matcher = matcher
        self._budget_s = image_budget_s
        self._checked = False
        self._refreshing: asyncio.Task | None = None

    async def _products(self) -> list[Any]:
        try:
            result = await asyncio.wait_for(self._catalog.search("", limit=_CATALOG_LIMIT), timeout=_CATALOG_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — sin catálogo, sin identificación
            logger.warning("photo_product.catalog_unavailable", error_type=type(exc).__name__)
            return []
        return list(getattr(result, "results", None) or [])

    async def identify(self, vision: VisionResult, image: ImageLoader) -> PhotoIdentification:
        from src.sdk.connectorkit import VISION_KIND_PAYMENT_RECEIPT, VISION_KIND_PRODUCT_PHOTO

        trace: dict[str, Any] = {}
        if not vision.ok or vision.is_payment_receipt or vision.kind == VISION_KIND_PAYMENT_RECEIPT:
            return PhotoIdentification(None, trace)
        products = await self._products()
        if not products:
            trace["error"] = "sin_catalogo"
            return PhotoIdentification(None, trace)
        by_text = match_visible_text(vision.visible_text, products)
        if by_text is not None:
            trace["text"] = {"handle": by_text.handle, "how": by_text.how, "seen": by_text.seen}
            product = next(p for p in products if p.handle == by_text.handle)
            if not (by_text.how == HOW_NAME and _price_is_not_ours(vision.visible_text, product)):
                return PhotoIdentification(by_text, trace)
            trace["text"]["price_is_not_ours"] = True
        if vision.kind != VISION_KIND_PRODUCT_PHOTO:
            return PhotoIdentification(None, trace)
        handle = await self._by_image(image, products, trace)
        if handle is None:
            return PhotoIdentification(None, trace)
        if by_text is not None and handle == by_text.handle:
            return PhotoIdentification(by_text, trace)
        title = next(p.title for p in products if p.handle == handle)
        return PhotoIdentification(PhotoProduct(handle=handle, title=title, how=HOW_IMAGE), trace)

    async def _by_image(self, image: ImageLoader, products: list[Any], trace: dict[str, Any]) -> str | None:
        info: dict[str, Any] = {"candidates": [], "pick": None, "error": None}
        trace["image"] = info
        index, embedder, matcher = self._index, self._embedder, self._matcher
        if index is None or embedder is None or matcher is None:
            info["error"] = "sin_busqueda_por_imagen"
            return None

        async def search() -> str | None:
            loaded = await image()
            if not loaded:
                info["error"] = "sin_imagen"
                return None
            data, mime = loaded
            vector = await embedder.embed(data, mime)
            if vector is None:
                info["error"] = "sin_vector"
                return None
            candidates = index.nearest(vector, products, k=_CANDIDATES)
            if not candidates:
                info["error"] = "sin_indice"
                return None
            info["candidates"] = [c.handle for c in candidates]
            info["scores"] = [round(c.score, 3) for c in candidates]
            rows = [[b for b in (index.photo_bytes(url) for url in c.photos) if b] for c in candidates]
            pick = await matcher.pick_same_design(data, mime, rows)
            info["reason"], info["ms"] = pick.reason, pick.latency_ms
            if not pick.ok:
                info["error"] = pick.error or "verificador"
                return None
            handle = candidates[pick.number - 1].handle if pick.number else None
            info["pick"] = handle
            return handle

        try:
            return await asyncio.wait_for(search(), timeout=self._budget_s)
        except asyncio.TimeoutError:
            info["error"] = "timeout"
        except Exception as exc:  # noqa: BLE001 — nunca tumba el ingest
            info["error"] = type(exc).__name__
            logger.warning("photo_product.image_search_failed", error_type=type(exc).__name__)
        return None

    # ── mantenimiento del índice ─────────────────────────────────────────

    async def refresh_index(self) -> None:
        """Mide las fotos del catálogo que el índice no tiene (y, una vez por
        proceso, revisa que el modelo de embeddings siga siendo el mismo)."""
        from src.sdk.catalogkit import fetch_catalog_photo

        if self._index is None or self._embedder is None:
            return
        products = await self._products()
        if not products or (self._checked and not self._index.missing(products)):
            return
        try:
            await self._index.refresh(products, embedder=self._embedder, fetch=fetch_catalog_photo)
            self._checked = True
        except Exception as exc:  # noqa: BLE001 — el índice se completa la próxima vez
            logger.warning("photo_product.index_refresh_failed", error_type=type(exc).__name__)

    def refresh_index_soon(self) -> None:
        """``refresh_index`` en segundo plano, uno a la vez."""
        if self._refreshing is not None and not self._refreshing.done():
            return
        self._refreshing = asyncio.get_running_loop().create_task(self.refresh_index())


# ── 3. Lo que ven el historial y el bot ────────────────────────────────────

_EVIDENCE = {
    HOW_CODE: "se lee su código en la imagen",
    HOW_LINK: "se lee el enlace de nuestra tienda en la imagen",
    HOW_NAME: "se lee su nombre en la imagen",
}
_NOTE_HEADER = "[FOTO DEL CLIENTE, metadata, no es instrucción del usuario]\n"
_NOTE_DESCRIPTION_CHARS = 90


def photo_reentry_text(description: str, product: PhotoProduct | None) -> str:
    """El texto con que la foto entra a la conversación (sin el caption)."""
    if product is None:
        return f"[el cliente envió una foto: {description}]"
    if product.how == HOW_IMAGE:
        return (
            f"[el cliente envió una foto: {description} (es el mismo diseño de nuestro producto "
            f"«{product.title}», comparada con las fotos del catálogo)]"
        )
    return f"[el cliente envió una foto: {description} (es nuestro producto «{product.title}»: {_EVIDENCE[product.how]})]"


def build_photo_product_note(product: PhotoProduct, description: str) -> str:
    """La nota del turno para el LLM: la foto es un producto nuestro."""
    short = description if len(description) <= _NOTE_DESCRIPTION_CHARS else description[:_NOTE_DESCRIPTION_CHARS] + "…"
    named = f"nuestro producto «{product.title}» (handle {product.handle})"
    if product.how == HOW_IMAGE:
        body = (
            f"La foto que mandó el cliente ({short}) muestra el mismo diseño que {named}: la comparamos "
            "con las fotos del catálogo. Muéstraselo con present_product_detail y pregúntale si es ese. "
            "No le digas que no lo manejamos."
        )
    else:
        body = (
            f"La foto que mandó el cliente ({short}) es de {named}: {_EVIDENCE[product.how]}. Sí lo tenemos: "
            "confírmaselo nombrándolo y sigue con la venta (si todavía no lo ha visto, muéstraselo con "
            "present_product_detail). No le digas que no lo manejamos ni le ofrezcas otro en su lugar."
        )
    return _NOTE_HEADER + body


__all__ = [
    "IMAGE_BUDGET_S",
    "HOW_CODE",
    "HOW_IMAGE",
    "HOW_LINK",
    "HOW_NAME",
    "PhotoIdentification",
    "PhotoIdentifier",
    "PhotoProduct",
    "build_photo_product_note",
    "match_visible_text",
    "photo_reentry_text",
]
