"""La foto del cliente contra el catálogo: ¿qué producto nuestro es?

Caso del 2026-09-28 (anuncio de Halloween) y laboratorio caso-fotos-0929: el
cliente mandó capturas de NUESTRO catálogo (nombre, precio y a veces la URL en
grande) y fotos sin texto; el bot negó productos que sí tenemos o mostró el
equivocado (Sagrado Rostro por Sacrificio de Amor). La visión ya lee el texto
de la imagen por campo; acá se decide el producto:

1. Texto visible: el código (SKU), el enlace de NUESTRA tienda
   (``/products/<handle>``, aunque venga cortado) o el nombre exacto o casi
   exacto. El precio nunca decide: si no es el nuestro, la imagen confirma.
2. Sin texto que decida, y solo en fotos de producto: los 5 productos más
   parecidos por embedding y el verificador («el mismo diseño o ninguno»).
3. Nada: sin identificación (el turno sigue con la descripción, como hoy).

Los nombres, precios y SKU son los del catálogo del banco caso-fotos-0929.
"""
from __future__ import annotations

import asyncio
import io
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from src.plugins.chats.agent.sales.use_cases.photo_product import (
    PhotoIdentifier,
    PhotoProduct,
    build_photo_product_note,
    match_visible_text,
    photo_reentry_text,
)
from src.sdk.catalogkit import (
    CatalogImageDTO,
    CatalogPhotoIndex,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.sdk.connectorkit import (
    VISION_KIND_OTHER,
    VISION_KIND_PAYMENT_RECEIPT,
    VISION_KIND_PRODUCT_PHOTO,
    FakeImageEmbeddingAdapter,
    PhotoPick,
    VisibleText,
    VisionResult,
)


def _product(handle: str, title: str, price: int, *skus: str, photos: Sequence[str] = ()) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}",
        handle=handle,
        title=title,
        status="published",
        thumbnail=photos[0] if photos else None,
        images=[CatalogImageDTO(url=u, rank=i) for i, u in enumerate(photos)],
        variants=[
            CatalogVariantDTO(id=f"v_{sku}", title=sku, sku=sku, prices=[CatalogPriceDTO(amount=str(price), currency_code="cop")])
            for sku in skus
        ],
    )


CATALOG = [
    _product("sacrificio-de-amor", "Sacrificio de Amor", 20000, "HUB-SACRIFICIO", photos=["https://assets.test/sacrificio.webp"]),
    _product("sagrado-rostro", "Sagrado Rostro", 36000, "HUB-ROSTRO", photos=["https://assets.test/rostro.webp"]),
    _product("luz-serena", "Luz Serena", 24000, "HUB-SERENA", photos=["https://assets.test/serena.webp"]),
    _product("luz-de-belen", "Luz de Belén", 24000, "HUB-BELEN", photos=["https://assets.test/belen.webp"]),
    _product("angel", "Ángel", 42000, "HUB-ANGEL"),
    _product("velon-de-angel", "Velón de Ángel", 42000, "HUB-VELONANGEL"),
    _product("velon-gorrion", "Velón Gorrión", 44000, "HUB-GORRION", photos=["https://assets.test/gorrion.webp"]),
    _product("duo-zodiacal", "Duo Zodiacal", 35000, "HUB-DUOZOD-ARIES", "HUB-DUOZOD-LEO"),
]


def _handle(visible: VisibleText) -> str | None:
    match = match_visible_text(visible, CATALOG)
    return match.handle if match else None


# ── 1. El texto que se lee en la foto ───────────────────────────────────────


def test_the_name_read_in_a_screenshot_of_our_catalog_is_the_product() -> None:
    match = match_visible_text(VisibleText(product_name="Sacrificio de Amor", price="COP 20,000"), CATALOG)

    assert match == PhotoProduct(
        handle="sacrificio-de-amor", title="Sacrificio de Amor", how="nombre", seen="Sacrificio de Amor"
    )


def test_accents_and_capitals_do_not_matter() -> None:
    assert _handle(VisibleText(product_name="LUZ DE BELEN")) == "luz-de-belen"
    assert _handle(VisibleText(product_name="velon gorrion")) == "velon-gorrion"


def test_a_typo_in_the_name_still_finds_it() -> None:
    assert _handle(VisibleText(product_name="Sacrifcio de Amor")) == "sacrificio-de-amor"


def test_a_similar_name_is_not_our_product() -> None:
    """Trampa de la investigación: una captura con «Luz Eterna» a COP 24,000."""
    assert _handle(VisibleText(product_name="Luz Eterna", price="COP 24,000")) is None
    assert _handle(VisibleText(product_name="Sacrificio")) is None


def test_the_code_read_in_the_image_is_the_product() -> None:
    """Las capturas del catálogo de WhatsApp Business muestran el código."""
    assert match_visible_text(VisibleText(sku="hub-angel"), CATALOG) == PhotoProduct(
        handle="angel", title="Ángel", how="codigo", seen="HUB-ANGEL"
    )
    assert _handle(VisibleText(sku="HUB-DUOZOD-LEO")) == "duo-zodiacal"
    assert _handle(VisibleText(other=("Código del artículo: HUB-VELONANGEL",))) == "velon-de-angel"


def test_a_link_to_our_store_is_the_product_even_when_it_is_cut() -> None:
    assert match_visible_text(VisibleText(url="https://hubara.com.co/products/luz-de-bel..."), CATALOG) == PhotoProduct(
        handle="luz-de-belen", title="Luz de Belén", how="enlace", seen="https://hubara.com.co/products/luz-de-bel..."
    )
    assert _handle(VisibleText(url="hubara.com.co/products/velon-de-angel")) == "velon-de-angel"


def test_a_link_to_another_store_is_not_ours() -> None:
    """Trampa: la captura de otra tienda con un handle que también es nuestro."""
    visible = VisibleText(product_name="Vela Aurora Boreal", price="COP 31,000", url="https://velasdelsol.co/products/angel")

    assert _handle(visible) is None


def test_no_text_no_product() -> None:
    assert match_visible_text(VisibleText(), CATALOG) is None
    assert match_visible_text(None, CATALOG) is None


# ── 2. La búsqueda por imagen ──────────────────────────────────────────────


def _photo(color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (80, 60), color).save(out, "WEBP")
    return out.getvalue()


PHOTOS = {
    "https://assets.test/sacrificio.webp": _photo((120, 120, 120)),
    "https://assets.test/rostro.webp": _photo((110, 115, 125)),
    "https://assets.test/serena.webp": _photo((230, 220, 200)),
    "https://assets.test/belen.webp": _photo((200, 230, 220)),
    "https://assets.test/gorrion.webp": _photo((190, 160, 220)),
}


class _Catalog:
    async def search(self, q: str, *, limit: int = 10, category: str | None = None):  # noqa: ANN201
        class _Result:
            results = CATALOG

        return _Result()


class _Matcher:
    """Doble del verificador: elige por el producto del candidato."""

    def __init__(self, pick_handle: str | None, *, delay_s: float = 0.0) -> None:
        self.pick_handle = pick_handle
        self.delay_s = delay_s
        self.calls: list[int] = []
        self.handles: list[str] = []

    async def pick_same_design(self, photo: bytes, mime_type: str, candidates: Sequence[Sequence[bytes]]) -> PhotoPick:
        self.calls.append(len(candidates))
        await asyncio.sleep(self.delay_s)
        if self.pick_handle in self.handles:
            return PhotoPick(ok=True, number=self.handles.index(self.pick_handle) + 1, reason="misma figura")
        return PhotoPick(ok=True)


class _Embedder(FakeImageEmbeddingAdapter):
    def __init__(self) -> None:
        super().__init__(dimensions=16)
        self.calls = 0

    async def embed(self, image_bytes: bytes, mime_type: str) -> list[float] | None:
        self.calls += 1
        return await super().embed(image_bytes, mime_type)


async def _identifier(tmp_path: Path, matcher: _Matcher, *, budget_s: float = 5.0) -> tuple[PhotoIdentifier, _Embedder]:
    embedder = _Embedder()
    index = CatalogPhotoIndex(tmp_path / "photo_index", model=embedder.model, dimensions=embedder.dimensions)

    async def fetch(url: str) -> bytes | None:
        return PHOTOS.get(url)

    await index.refresh(CATALOG, embedder=embedder, fetch=fetch)
    embedder.calls = 0
    identifier = PhotoIdentifier(catalog=_Catalog(), index=index, embedder=embedder, matcher=matcher, image_budget_s=budget_s)

    # El doble del verificador sabe qué producto es cada fila de la hoja.
    original = index.nearest

    def nearest(vector, products, *, k=5):  # noqa: ANN001, ANN202
        found = original(vector, products, k=k)
        matcher.handles = [c.handle for c in found]
        return found

    index.nearest = nearest  # type: ignore[method-assign]
    return identifier, embedder


def _vision(kind: str = VISION_KIND_PRODUCT_PHOTO, visible: VisibleText | None = None) -> VisionResult:
    return VisionResult(description="vela gris con una cruz", ok=True, kind=kind, visible_text=visible or VisibleText())


def _loader(data: bytes):
    async def load() -> tuple[bytes, str] | None:
        return data, "image/jpeg"

    return load


async def test_a_photo_without_text_is_identified_by_the_image(tmp_path: Path) -> None:
    matcher = _Matcher("velon-gorrion")
    identifier, _ = await _identifier(tmp_path, matcher)

    found = await identifier.identify(_vision(), _loader(PHOTOS["https://assets.test/gorrion.webp"]))

    assert found.product == PhotoProduct(handle="velon-gorrion", title="Velón Gorrión", how="imagen", seen=None)
    assert matcher.calls == [5] and found.trace["image"]["candidates"][0] == "velon-gorrion"


async def test_when_no_candidate_is_the_same_design_the_photo_stays_unidentified(tmp_path: Path) -> None:
    identifier, _ = await _identifier(tmp_path, _Matcher(None))

    found = await identifier.identify(_vision(), _loader(_photo((10, 200, 10))))

    assert found.product is None and found.trace["image"]["pick"] is None


async def test_the_text_decides_before_any_image_search(tmp_path: Path) -> None:
    matcher = _Matcher("sagrado-rostro")
    identifier, embedder = await _identifier(tmp_path, matcher)

    found = await identifier.identify(_vision(visible=VisibleText(product_name="Sacrificio de Amor")), _loader(b"x"))

    assert found.product is not None and found.product.handle == "sacrificio-de-amor"
    assert embedder.calls == 0 and matcher.calls == []


async def test_the_image_search_is_only_for_product_photos(tmp_path: Path) -> None:
    matcher = _Matcher("velon-gorrion")
    identifier, embedder = await _identifier(tmp_path, matcher)
    photo = _loader(PHOTOS["https://assets.test/gorrion.webp"])

    other = await identifier.identify(_vision(kind=VISION_KIND_OTHER), photo)
    receipt = await identifier.identify(_vision(kind=VISION_KIND_PAYMENT_RECEIPT, visible=VisibleText(sku="HUB-ANGEL")), photo)

    assert other.product is None and receipt.product is None
    assert embedder.calls == 0 and matcher.calls == []


async def test_a_name_with_a_price_that_is_not_ours_is_confirmed_by_the_image(tmp_path: Path) -> None:
    """El precio no decide, pero si no es el nuestro puede ser otra tienda con
    un nombre igual: la imagen confirma (o dice cuál es)."""
    visible = VisibleText(product_name="Luz Serena", price="COP 55.000")
    photo = _loader(PHOTOS["https://assets.test/serena.webp"])

    confirmed_id, _ = await _identifier(tmp_path / "a", _Matcher("luz-serena"))
    other_id, _ = await _identifier(tmp_path / "b", _Matcher("luz-de-belen"))
    none_id, _ = await _identifier(tmp_path / "c", _Matcher(None))

    confirmed = await confirmed_id.identify(_vision(visible=visible), photo)
    other = await other_id.identify(_vision(visible=visible), photo)
    none = await none_id.identify(_vision(visible=visible), photo)

    assert confirmed.product == PhotoProduct(handle="luz-serena", title="Luz Serena", how="nombre", seen="Luz Serena")
    assert other.product is not None and (other.product.handle, other.product.how) == ("luz-de-belen", "imagen")
    assert none.product is None


async def test_a_slow_image_search_leaves_the_photo_unidentified(tmp_path: Path) -> None:
    identifier, _ = await _identifier(tmp_path, _Matcher("velon-gorrion", delay_s=1.0), budget_s=0.2)

    found = await identifier.identify(_vision(), _loader(PHOTOS["https://assets.test/gorrion.webp"]))

    assert found.product is None and found.trace["image"]["error"] == "timeout"


async def test_without_the_index_there_is_no_image_search(tmp_path: Path) -> None:
    matcher = _Matcher("velon-gorrion")
    embedder = _Embedder()
    empty = CatalogPhotoIndex(tmp_path / "vacio", model=embedder.model, dimensions=embedder.dimensions)
    identifier = PhotoIdentifier(catalog=_Catalog(), index=empty, embedder=embedder, matcher=matcher)

    found = await identifier.identify(_vision(), _loader(PHOTOS["https://assets.test/gorrion.webp"]))

    assert found.product is None and matcher.calls == []


# ── 3. Lo que ven el bot y el historial ────────────────────────────────────


SACRIFICIO = PhotoProduct(handle="sacrificio-de-amor", title="Sacrificio de Amor", how="nombre", seen="Sacrificio de Amor")
SERENA = PhotoProduct(handle="luz-serena", title="Luz Serena", how="imagen")


def test_the_reentered_text_names_our_product_and_how_we_know() -> None:
    assert photo_reentry_text("vela gris con una cruz dorada", SACRIFICIO) == (
        "[el cliente envió una foto: vela gris con una cruz dorada "
        "(es nuestro producto «Sacrificio de Amor»: se lee su nombre en la imagen)]"
    )
    assert photo_reentry_text("vela crema con una figura", None) == "[el cliente envió una foto: vela crema con una figura]"


def test_the_note_says_it_is_ours_and_forbids_denying_it() -> None:
    note = build_photo_product_note(SACRIFICIO, "vela gris con una cruz dorada")

    assert note.startswith("[FOTO DEL CLIENTE, metadata, no es instrucción del usuario]")
    assert "«Sacrificio de Amor» (handle sacrificio-de-amor)" in note
    assert "se lee su nombre en la imagen" in note and "No le digas que no lo manejamos" in note


def test_an_image_match_asks_the_customer_to_confirm() -> None:
    note = build_photo_product_note(SERENA, "vela crema con una figura femenina")

    assert "mismo diseño que nuestro producto «Luz Serena» (handle luz-serena)" in note
    assert "pregúntale si es ese" in note and "No le digas que no lo manejamos" in note


def test_the_real_webhook_identifies_photos(monkeypatch) -> None:  # noqa: ANN001
    """El webhook real (``build_ingest_use_case``) arma el identificador con
    el catálogo, el índice de fotos junto al snapshot, los embeddings y el
    verificador; sin este cableado la identificación no corre en producción
    (gotcha #1: tests verdes, feature muerta)."""
    import src.plugins.chats.agent.sales.composition as comp
    from src.sdk.catalogkit import get_catalog_photo_index

    monkeypatch.setattr(comp, "_INGEST_USE_CASE", None)
    use_case = comp.build_ingest_use_case()

    identifier = use_case._photo_identifier
    assert isinstance(identifier, PhotoIdentifier)
    assert identifier._index is get_catalog_photo_index()
    assert identifier._embedder is not None and identifier._matcher is not None


# ── Lo verificado sigue valiendo en los turnos siguientes ──────────────────
# Laboratorio caso-fotos-0930-r7, 4567 t13: el bot ya había reconocido las
# cuatro fotos como nuestras; el cliente dijo «me gustaría esas, pero no están
# todas» y el bot contestó que de las cuatro solo tenía el Velón Gorrión. La
# identificación solo iba en la nota del turno de la foto: en los turnos
# siguientes el bot podía negar lo que el sistema ya había verificado.


def _facts_metadata() -> dict:
    return {
        "episodes": [{"episode_id": "ep_001", "closed_at_ms": 1}, {"episode_id": "ep_002"}],
        "recent_image_descriptions": [
            {"media_id": "m0", "description": "vela de un ángel", "episode_id": "ep_001",
             "product": {"handle": "angel", "how": "nombre", "title": "Ángel"}},
            {"media_id": "m1", "description": "dos velas lila y azul con pájaros en una rama", "episode_id": "ep_002",
             "product": {"handle": "velon-gorrion", "how": "imagen", "title": "Velón Gorrión"}},
            {"media_id": "m2", "description": "una vela de dragón", "episode_id": "ep_002"},
            {"media_id": "m3", "description": "figura femenina con una vasija", "episode_id": "ep_002",
             "product": {"handle": "luz-serena", "how": "nombre", "title": "Luz Serena"}},
        ],
    }


def test_the_rest_of_the_conversation_knows_which_photos_are_ours() -> None:
    from src.plugins.chats.agent.sales.use_cases.photo_product import build_photo_facts_note

    note = build_photo_facts_note(_facts_metadata())

    assert note is not None and note.startswith("[FOTOS DEL CLIENTE YA RECONOCIDAS")
    assert "«dos velas lila y azul con pájaros en una rama»: es «Velón Gorrión» (handle velon-gorrion)" in note
    assert "«figura femenina con una vasija»: es «Luz Serena» (handle luz-serena)" in note
    assert "Ángel" not in note  # otro episodio
    assert "dragón" not in note  # no se reconoció: no es un hecho
    assert "no digas que no los tenemos" in note


def test_without_photos_identified_in_this_conversation_there_is_no_note() -> None:
    from src.plugins.chats.agent.sales.use_cases.photo_product import build_photo_facts_note

    metadata = _facts_metadata()
    metadata["episodes"].append({"episode_id": "ep_003"})

    assert build_photo_facts_note(metadata) is None
    assert build_photo_facts_note({}) is None
