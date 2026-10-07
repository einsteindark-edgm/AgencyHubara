"""«¿Qué productos tienen?»: el catálogo completo en UN mensaje, o sus
categorías primero (incidente 2026-10-06, decisión del operador).

El bot hizo `search_products(limit=30)` (31 productos: uno nunca aparecía) y
`present_products` con 29 handles, que salió en TRES listas. Ahora
`present_products` sin handles lee el catálogo completo de la copia local
(el mismo cliente que `search_products`):

  * si cabe en la lista de productos de WhatsApp (hasta 30 productos en hasta
    10 secciones), sale en UN mensaje con los productos por categoría;
  * si no cabe, el cliente recibe sus categorías (una fila por categoría con
    cuántos productos tiene) y, cuando elige una, el LLM la recibe clara y
    `present_products(category=…)` le muestra los productos de esa categoría.

Lo que el catálogo de Meta no tiene (sin foto o sin precio: el criterio del
push) no entra al mensaje. Todo determinístico: el id de la fila decide,
nunca el texto del cliente.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext
from loguru import logger

from src.platform.catalog import CatalogImageDTO, CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.meta_catalog.mapper import map_products_batch
from src.plugins.chats.agent.sales.parsers import parse_whatsapp_inbound
from src.plugins.chats.agent.sales.tools import ui_intents
from src.plugins.chats.agent.sales.tools.ui_intents import PresentProductsTool
from src.plugins.chats.agent.sales.translate import translate_to_effective_text
from src.sdk.connectorkit import product_retailer_id

SESSION = "wa_573001234567"


def _raw(handle: str, slug: str | None, label: str | None, *, price: bool = True, photo: bool = True) -> dict[str, Any]:
    return {
        "id": f"prod_{handle}",
        "handle": handle,
        "title": handle.replace("-", " ").title(),
        "status": "published",
        "thumbnail": f"https://img.test/{handle}.webp" if photo else None,
        "variants": [
            {
                "id": f"variant_{handle}",
                "title": "Unico",
                "sku": f"HUB-{handle.upper()}",
                "prices": [{"amount": "23000", "currency_code": "cop"}] if price else [],
            }
        ],
        "categories": [slug] if slug else [],
        "category_labels": {slug: label} if slug and label else None,
    }


def _group(n: int, slug: str, label: str) -> list[dict[str, Any]]:
    return [_raw(f"{slug}-{i:02d}", slug, label) for i in range(n)]


def _catalog(tmp_path: Path, raw_products: list[dict[str, Any]]) -> LocalSnapshotCatalogClient:
    snap = tmp_path / "catalog"
    snap.mkdir()
    (snap / "snapshot.json").write_text(json.dumps(raw_products), encoding="utf-8")
    (snap / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v-test",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "product_count": len(raw_products),
            }
        ),
        encoding="utf-8",
    )
    return LocalSnapshotCatalogClient(snap)


def _tool(tmp_path: Path, raw_products: list[dict[str, Any]]) -> PresentProductsTool:
    return PresentProductsTool(workspace=str(tmp_path), catalog=_catalog(tmp_path, raw_products))


async def _call(tool: PresentProductsTool, **kwargs: Any) -> dict[str, Any]:
    ctx = ToolContext(session_key=SESSION, channel="whatsapp", chat_id="c")
    return json.loads(await tool.execute_with_context(ctx, **kwargs))


def _intents(vault: Path) -> list[dict[str, Any]]:
    path = vault / SESSION / "metadata.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("pending_ui_intents") or []


def _rows(intent: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for s in intent["params"]["sections"] for r in s["rows"]]


def _list_reply(row_id: str, title: str) -> dict[str, Any]:
    """El webhook de Meta cuando el cliente elige una fila de una lista."""
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "metadata": {"phone_number_id": "PID"},
                    "messages": [{
                        "id": "wamid.LIST",
                        "from": "573001234567",
                        "timestamp": "1714312345",
                        "type": "interactive",
                        "interactive": {
                            "type": "list_reply",
                            "list_reply": {"id": row_id, "title": title, "description": "12 productos"},
                        },
                    }],
                },
            }],
        }],
    }


async def _choice(row: dict[str, Any]) -> str:
    """Lo que llega al LLM cuando el cliente toca esa fila."""
    effective = await translate_to_effective_text(parse_whatsapp_inbound(_list_reply(row["id"], row["title"])))
    return effective.text


def _category_in(choice_text: str) -> str:
    """El `category` que el LLM copia de la elección."""
    return choice_text.split('category="')[1].rsplit('"', 1)[0]


# 29 productos en 3 categorías: cabe.
CATALOG_29 = (
    _group(12, "velones", "Velones")
    + _group(10, "religiosas", "Religiosas")
    + _group(7, "aromaticas", "Aromáticas")
)
# 31 productos: no cabe en un mensaje.
CATALOG_31 = (
    _group(12, "velones", "Velones")
    + _group(11, "religiosas", "Religiosas")
    + _group(8, "aromaticas", "Aromáticas")
)


@pytest.mark.asyncio
async def test_whats_in_the_catalog_with_29_products_goes_in_one_message_by_category(
    tmp_path: Path, _isolate_vault_dir: Path
):
    tool = _tool(tmp_path, CATALOG_29)

    out = await _call(tool, intro_text="Este es nuestro catálogo:", group_by="categories")

    [intent] = _intents(_isolate_vault_dir)
    assert intent["kind"] == "products_list"
    assert {s["title"]: len(s["rows"]) for s in intent["params"]["sections"]} == {
        "Velones": 12, "Religiosas": 10, "Aromáticas": 7,
    }
    assert out["queued"] is True and out["count"] == 29
    # El LLM sabe qué productos vio el cliente (para anotar el que elija).
    assert [p["handle"] for p in out["products"]] == [p["handle"] for p in CATALOG_29]


@pytest.mark.asyncio
async def test_the_whole_catalog_needs_no_search_first(tmp_path: Path, _isolate_vault_dir: Path):
    """Sin handles ni `group_by`: es el catálogo completo (antes la tool exigía
    los handles de una búsqueda)."""
    tool = _tool(tmp_path, CATALOG_29)

    out = await _call(tool, intro_text="Este es nuestro catálogo:")

    assert out["queued"] is True
    assert len(_rows(_intents(_isolate_vault_dir)[0])) == 29


@pytest.mark.asyncio
async def test_with_31_products_the_customer_gets_the_categories_first(
    tmp_path: Path, _isolate_vault_dir: Path
):
    tool = _tool(tmp_path, CATALOG_31)

    out = await _call(tool, intro_text="Con gusto te muestro el catálogo.", group_by="categories")

    [intent] = _intents(_isolate_vault_dir)
    assert intent["kind"] == "categories"
    assert [(r["id"], r["title"], r["description"]) for r in _rows(intent)] == [
        ("categoria:aromaticas", "Aromáticas", "8 productos"),
        ("categoria:religiosas", "Religiosas", "11 productos"),
        ("categoria:velones", "Velones", "12 productos"),
    ]
    assert intent["params"]["intro_text"] == "Con gusto te muestro el catálogo."
    # El LLM sabe qué se mostró y qué hacer cuando el cliente elija.
    assert out["queued"] is True and out["kind"] == "categories"
    assert out["categories"] == [
        {"name": "Aromáticas", "products": 8},
        {"name": "Religiosas", "products": 11},
        {"name": "Velones", "products": 12},
    ]
    assert "present_products(category=" in out["summary"]
    assert "[el cliente eligió la categoría:" in out["summary"]


@pytest.mark.asyncio
async def test_choosing_a_category_reaches_the_llm_clearly_and_shows_its_products(
    tmp_path: Path, _isolate_vault_dir: Path
):
    tool = _tool(tmp_path, CATALOG_31)
    await _call(tool, intro_text="Con gusto te muestro el catálogo.")
    velones = next(r for r in _rows(_intents(_isolate_vault_dir)[0]) if r["title"] == "Velones")

    # 1) El cliente toca la fila «Velones»: el LLM la recibe como categoría,
    #    con el id de la fila (el que se salta el motor: ya dice cuál es).
    msg = parse_whatsapp_inbound(_list_reply(velones["id"], velones["title"]))
    effective = await translate_to_effective_text(msg)
    assert effective.text == '[el cliente eligió la categoría: Velones (category="categoria:velones")]'
    assert effective.structured_payload["id"] == "categoria:velones"

    # 2) El LLM le pide a la tool los productos de esa categoría.
    out = await _call(tool, intro_text="Nuestros velones:", category=_category_in(effective.text))

    intents = _intents(_isolate_vault_dir)
    assert len(intents) == 2
    shown = intents[1]
    assert shown["kind"] == "products_list"
    assert [r["id"] for r in _rows(shown)] == [p["handle"] for p in CATALOG_31[:12]]
    assert [s["title"] for s in shown["params"]["sections"]] == ["Velones"]
    # El encabezado del catálogo lo arma el código (no es texto del LLM).
    assert shown["params"]["category"] == "Velones" and "header_text" not in shown["params"]
    assert out["queued"] is True and out["count"] == 12 and out["category"] == "Velones"


@pytest.mark.asyncio
async def test_a_product_row_still_arrives_as_a_product_selection():
    msg = parse_whatsapp_inbound(_list_reply("velones-00", "Velones 00"))

    effective = await translate_to_effective_text(msg)

    assert effective.text == "[el cliente seleccionó: Velones 00]"


@pytest.mark.asyncio
async def test_a_category_with_more_than_30_products_shows_30_and_says_there_are_more(
    tmp_path: Path, _isolate_vault_dir: Path
):
    tool = _tool(tmp_path, _group(34, "velones", "Velones") + _group(3, "aromaticas", "Aromáticas"))

    out = await _call(tool, intro_text="Nuestros velones:", category="Velones")

    [intent] = _intents(_isolate_vault_dir)
    assert len(_rows(intent)) == 30
    assert out["count"] == 30 and out["total"] == 34
    assert "4 más" in out["summary"]


@pytest.mark.asyncio
async def test_more_than_10_categories_the_menu_shows_10_and_names_the_rest(
    tmp_path: Path, _isolate_vault_dir: Path
):
    """La lista de WhatsApp acepta 10 filas: las 10 categorías con más
    productos van en el menú y las demás, nombradas en su texto (el cliente
    puede escribir cualquiera)."""
    raw = [p for i in range(12) for p in _group(3 if i < 10 else 2, f"cat-{i:02d}", f"Cat {i:02d}")]
    tool = _tool(tmp_path, raw)

    out = await _call(tool, intro_text="Con gusto te muestro el catálogo.")

    [intent] = _intents(_isolate_vault_dir)
    assert intent["kind"] == "categories"
    assert [r["title"] for r in _rows(intent)] == [f"Cat {i:02d}" for i in range(10)]
    assert intent["params"]["more_categories"] == ["Cat 10", "Cat 11"]
    assert len(out["categories"]) == 12, "el LLM ve la lista cerrada completa"


@pytest.mark.asyncio
async def test_products_without_a_category_get_their_own_row(tmp_path: Path, _isolate_vault_dir: Path):
    raw = _group(15, "velones", "Velones") + _group(14, "aromaticas", "Aromáticas") + [
        _raw("sin-categoria-1", None, None),
        _raw("sin-categoria-2", None, None),
    ]
    tool = _tool(tmp_path, raw)

    await _call(tool, intro_text="Con gusto te muestro el catálogo.")
    menu = _rows(_intents(_isolate_vault_dir)[0])
    assert menu[-1]["title"] == "Otros" and menu[-1]["description"] == "2 productos"

    # Elegirla muestra esos productos.
    out = await _call(tool, intro_text="Estos son:", category=_category_in(await _choice(menu[-1])))

    assert [r["id"] for r in _rows(_intents(_isolate_vault_dir)[1])] == ["sin-categoria-1", "sin-categoria-2"]
    assert out["count"] == 2


@pytest.mark.asyncio
async def test_an_unknown_category_shows_nothing_and_offers_the_real_ones(
    tmp_path: Path, _isolate_vault_dir: Path
):
    tool = _tool(tmp_path, CATALOG_31)

    out = await _call(tool, intro_text="Mira:", category="zapatos")

    assert _intents(_isolate_vault_dir) == []
    assert out["queued"] is False and out["error"] == "category_not_found"
    assert out["available"] == ["Aromáticas", "Religiosas", "Velones"]


@pytest.mark.asyncio
async def test_handles_win_over_the_whole_catalog(tmp_path: Path, _isolate_vault_dir: Path):
    """Con handles (4 o más productos puntuales) la tool muestra esos, como siempre."""
    tool = _tool(tmp_path, CATALOG_31)

    out = await _call(tool, handles=["velones-00", "religiosas-03"], intro_text="Estas dos:")

    [intent] = _intents(_isolate_vault_dir)
    assert [r["id"] for r in _rows(intent)] == ["velones-00", "religiosas-03"]
    assert out["count"] == 2


# ── Revisión del PR #394: casos borde ─────────────────────────────────────


@pytest.mark.asyncio
async def test_one_category_that_does_not_fit_skips_the_menu_of_one_row(tmp_path: Path, _isolate_vault_dir: Path):
    """31 productos en UNA categoría: un menú de una sola fila sobra."""
    tool = _tool(tmp_path, _group(31, "velones", "Velones"))

    out = await _call(tool, intro_text="Este es nuestro catálogo:")

    [intent] = _intents(_isolate_vault_dir)
    assert intent["kind"] == "products_list" and len(_rows(intent)) == 30
    assert out["category"] == "Velones" and out["count"] == 30 and out["total"] == 31
    assert "1 más" in out["summary"]


@pytest.mark.asyncio
async def test_a_real_otros_category_and_products_without_one_share_a_single_row(
    tmp_path: Path, _isolate_vault_dir: Path
):
    raw = _group(20, "velones", "Velones") + _group(8, "otros", "Otros") + [
        _raw(f"suelta-{i}", None, None) for i in range(4)
    ]
    tool = _tool(tmp_path, raw)

    await _call(tool, intro_text="Con gusto te muestro el catálogo.")
    menu = _rows(_intents(_isolate_vault_dir)[0])
    assert [(r["title"], r["description"]) for r in menu] == [("Velones", "20 productos"), ("Otros", "12 productos")]

    out = await _call(tool, intro_text="Estos son:", category=_category_in(await _choice(menu[-1])))

    assert out["count"] == 12
    assert sorted(r["id"] for r in _rows(_intents(_isolate_vault_dir)[1])) == sorted(
        [f"otros-{i:02d}" for i in range(8)] + [f"suelta-{i}" for i in range(4)]
    )


@pytest.mark.asyncio
async def test_what_meta_does_not_have_stays_out_and_todays_catalog_fits_in_one_message(
    tmp_path: Path, _isolate_vault_dir: Path
):
    """Producción hoy: 31 productos y uno sin precio. El push a Meta lo salta;
    en el mensaje del catálogo haría que Meta lo rechace (y caería a 3 listas,
    el síntoma del incidente) o lo descarte en silencio. No entra: quedan 30
    y caben en UN mensaje. El log y el LLM saben cuál quedó afuera."""
    raw = _group(16, "velones", "Velones") + _group(14, "religiosas", "Religiosas") + [
        _raw("sin-precio", "velones", "Velones", price=False)
    ]
    tool = _tool(tmp_path, raw)
    warnings: list[str] = []
    sink = logger.add(warnings.append, level="WARNING", format="{message}")
    try:
        out = await _call(tool, intro_text="Este es nuestro catálogo:")
    finally:
        logger.remove(sink)

    [intent] = _intents(_isolate_vault_dir)
    assert intent["kind"] == "products_list" and len(_rows(intent)) == 30
    assert "sin-precio" not in [r["id"] for r in _rows(intent)]
    assert out["incomplete"] == [{"handle": "sin-precio", "title": "Sin Precio", "lacks": "precio"}]
    assert any("sin-precio" in w for w in warnings)


def _product(**overrides: Any) -> CatalogProductDTO:
    base: dict[str, Any] = {
        "id": "prod_x", "handle": "x", "title": "X", "status": "published",
        "thumbnail": "https://img.test/x.webp",
        "variants": [CatalogVariantDTO(id="v_x", title="Unico", sku="HUB-X",
                                       prices=[CatalogPriceDTO(amount="23000", currency_code="cop")])],
    }
    base.update(overrides)
    return CatalogProductDTO(**base)


_TWO_SIGNS = {"Signo": ["Leo", "Libra"]}


@pytest.mark.parametrize(
    "product",
    [
        _product(),
        _product(thumbnail=None),
        _product(thumbnail=None, images=[CatalogImageDTO(url="https://img.test/x-1.webp")]),
        _product(variants=[CatalogVariantDTO(id="v_x", title="Unico", sku="HUB-X", prices=[])]),
        _product(variants=[CatalogVariantDTO(id="v_x", title="Unico", sku="HUB-X",
                                             prices=[CatalogPriceDTO(amount="", currency_code="cop")])]),
        _product(variants=[CatalogVariantDTO(id="v_x", title="Unico", sku="HUB-X",
                                             prices=[CatalogPriceDTO(amount="35000", currency_code="usd")])]),
        _product(options=_TWO_SIGNS, variants=[
            CatalogVariantDTO(id="v_leo", title="Leo", sku="HUB-X-LEO", options={"Signo": "Leo"},
                              prices=[CatalogPriceDTO(amount="35000", currency_code="cop")]),
            CatalogVariantDTO(id="v_libra", title="Libra", sku="HUB-X-LIBRA", options={"Signo": "Libra"},
                              prices=[CatalogPriceDTO(amount="35000", currency_code="cop")]),
        ]),
        _product(options=_TWO_SIGNS, variants=[
            CatalogVariantDTO(id="v_leo", title="Leo", sku="HUB-X-LEO", options={"Signo": "Leo"}, prices=[]),
            CatalogVariantDTO(id="v_libra", title="Libra", sku="HUB-X-LIBRA", options={"Signo": "Libra"},
                              prices=[CatalogPriceDTO(amount="35000", currency_code="cop")]),
        ]),
    ],
)
def test_the_catalog_message_uses_the_same_rule_as_the_meta_push(product: CatalogProductDTO) -> None:
    """La fila del producto entra al mensaje del catálogo si y solo si el push
    a Meta publica el ítem que esa fila nombra (su `product_retailer_id`)."""
    mapped, _skipped = map_products_batch([product])

    in_meta = product_retailer_id(product) in {item.retailer_id for item in mapped}

    assert (ui_intents._lacks(product) is None) == in_meta


@pytest.mark.asyncio
async def test_an_empty_otros_shows_nothing(tmp_path: Path, _isolate_vault_dir: Path):
    tool = _tool(tmp_path, CATALOG_31)

    out = await _call(tool, intro_text="Estos son:", category="categoria:_sin_categoria")

    assert out["queued"] is False and out["error"] == "category_empty"
    assert _intents(_isolate_vault_dir) == []


@pytest.mark.asyncio
async def test_a_catalog_without_categories_finds_the_category_by_text(tmp_path: Path, _isolate_vault_dir: Path):
    raw = [_raw(f"vela-religiosa-{i}", None, None) for i in range(3)] + [_raw(f"vela-aroma-{i}", None, None) for i in range(3)]
    tool = _tool(tmp_path, raw)

    out = await _call(tool, intro_text="Estas son:", category="religiosas")

    [intent] = _intents(_isolate_vault_dir)
    assert [r["id"] for r in _rows(intent)] == [f"vela-religiosa-{i}" for i in range(3)]
    assert out["queued"] is True and out["count"] == 3


@pytest.mark.asyncio
async def test_without_the_copy_of_the_catalog_nothing_is_sent(tmp_path: Path, _isolate_vault_dir: Path):
    tool = PresentProductsTool(workspace=str(tmp_path), catalog=LocalSnapshotCatalogClient(tmp_path / "no-existe"))

    out = await _call(tool, intro_text="Este es nuestro catálogo:")

    assert out["queued"] is False and out["error"] == "catalog_unavailable"
    assert _intents(_isolate_vault_dir) == []


@pytest.mark.asyncio
async def test_an_empty_catalog_sends_nothing(tmp_path: Path, _isolate_vault_dir: Path):
    out = await _call(_tool(tmp_path, []), intro_text="Este es nuestro catálogo:")

    assert out["queued"] is False and out["error"] == "empty_catalog"
    assert _intents(_isolate_vault_dir) == []


@pytest.mark.asyncio
async def test_more_than_30_products_without_categories_show_30_and_say_how_many_more(
    tmp_path: Path, _isolate_vault_dir: Path
):
    tool = _tool(tmp_path, [_raw(f"vela-{i:02d}", None, None) for i in range(35)])

    out = await _call(tool, intro_text="Este es nuestro catálogo:")

    [intent] = _intents(_isolate_vault_dir)
    assert intent["kind"] == "products_list" and len(_rows(intent)) == 30
    assert out["count"] == 30 and out["total"] == 35
    assert "5 productos más" in out["summary"]


@pytest.mark.asyncio
async def test_with_the_meta_catalog_the_llm_hears_cart_and_card_not_a_row_selection(
    tmp_path: Path, monkeypatch, _isolate_vault_dir: Path
):
    """En el catálogo de Meta el cliente no «selecciona una fila»: agrega al
    carrito o escribe desde la ficha. Si WhatsApp lo rechaza, sale como lista
    (en páginas de a 10) y ahí sí elige una fila."""
    monkeypatch.setenv("META_CATALOG_ID", "CAT_TEST")

    out = await _call(_tool(tmp_path, CATALOG_29), intro_text="Este es nuestro catálogo:")

    assert out["pages"] == 1
    assert "un mensaje" in out["summary"] and "carrito" in out["summary"]
    # Lista de respaldo: 12, 10 y 7 filas van en 4 páginas (cada categoría entera si cabe).
    assert "4 mensajes" in out["summary"] and "[el cliente seleccionó: <título>]" in out["summary"]


@pytest.mark.asyncio
async def test_without_the_meta_catalog_the_llm_hears_it_is_a_list_and_how_many_messages(
    tmp_path: Path, monkeypatch, _isolate_vault_dir: Path
):
    monkeypatch.delenv("META_CATALOG_ID", raising=False)

    out = await _call(_tool(tmp_path, CATALOG_29), intro_text="Este es nuestro catálogo:")

    assert out["pages"] == 4
    assert "4 mensajes" in out["summary"] and "[el cliente seleccionó: <título>]" in out["summary"]
    assert "carrito" not in out["summary"]


# ── Segunda revisión del PR #394 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_without_the_meta_catalog_a_product_without_photo_still_goes_in_the_list(
    tmp_path: Path, monkeypatch, _isolate_vault_dir: Path
):
    """La foto la pide el catálogo de Meta; la lista de respaldo (texto) no.
    Sin `META_CATALOG_ID` un producto con precio y sin foto se muestra."""
    monkeypatch.delenv("META_CATALOG_ID", raising=False)
    raw = [_raw(f"vela-{i}", "velas", "Velas", photo=False) for i in range(5)] + _group(2, "velas", "Velas")

    out = await _call(_tool(tmp_path, raw), intro_text="Este es nuestro catálogo:")

    [intent] = _intents(_isolate_vault_dir)
    assert len(_rows(intent)) == 7
    assert out["queued"] is True and "incomplete" not in out


@pytest.mark.asyncio
async def test_without_the_meta_catalog_a_catalog_without_photos_is_shown(
    tmp_path: Path, monkeypatch, _isolate_vault_dir: Path
):
    monkeypatch.delenv("META_CATALOG_ID", raising=False)
    raw = [_raw(f"vela-{i}", "velas", "Velas", photo=False) for i in range(5)]

    out = await _call(_tool(tmp_path, raw), intro_text="Este es nuestro catálogo:")

    assert out["queued"] is True and out["count"] == 5


@pytest.mark.asyncio
async def test_with_the_meta_catalog_a_product_without_photo_stays_out(
    tmp_path: Path, monkeypatch, _isolate_vault_dir: Path
):
    monkeypatch.setenv("META_CATALOG_ID", "CAT_TEST")
    raw = _group(2, "velas", "Velas") + [_raw("sin-foto", "velas", "Velas", photo=False)]

    out = await _call(_tool(tmp_path, raw), intro_text="Este es nuestro catálogo:")

    [intent] = _intents(_isolate_vault_dir)
    assert [r["id"] for r in _rows(intent)] == ["velas-00", "velas-01"]
    assert out["incomplete"] == [{"handle": "sin-foto", "title": "Sin Foto", "lacks": "foto"}]


@pytest.mark.asyncio
async def test_a_category_whose_products_are_all_incomplete_says_they_exist(
    tmp_path: Path, _isolate_vault_dir: Path
):
    """Antes: `category_empty` sin lista, y el bot podía decir «no tenemos»
    de algo que existe. Ahora dice cuáles y qué les falta."""
    raw = _group(20, "velones", "Velones") + [
        _raw(f"religiosa-{i:02d}", "religiosas", "Religiosas", price=False) for i in range(12)
    ]

    out = await _call(_tool(tmp_path, raw), intro_text="Estas son:", category="categoria:religiosas")

    assert _intents(_isolate_vault_dir) == []
    assert out["queued"] is False and out["error"] == "incomplete_products"
    assert [x["handle"] for x in out["incomplete"]] == [f"religiosa-{i:02d}" for i in range(12)]
    assert {x["lacks"] for x in out["incomplete"]} == {"precio"}
    assert "Religiosas" in out["message"]


@pytest.mark.asyncio
async def test_a_catalog_without_categories_keeps_the_typed_text_out_of_the_message(
    tmp_path: Path, _isolate_vault_dir: Path
):
    """Sin categorías cargadas, lo que pasó el LLM (o escribió el cliente) no
    va de encabezado ni de título de sección: no pasaría por la guarda."""
    raw = [_raw(f"vela-religiosa-{i}", None, None) for i in range(3)]

    out = await _call(_tool(tmp_path, raw), intro_text="Estas son:", category="religiosa")

    [intent] = _intents(_isolate_vault_dir)
    assert "category" not in intent["params"] and "header_text" not in intent["params"]
    assert [s["title"] for s in intent["params"]["sections"]] == ["Productos"]
    assert out["queued"] is True and out["count"] == 3


@pytest.mark.asyncio
async def test_if_the_engine_fails_the_rule_of_today_decides(
    tmp_path: Path, monkeypatch, _isolate_vault_dir: Path
):
    """Una falla del motor no es «catálogo no disponible»: decide la regla de hoy."""
    async def _broken(*_args, **_kwargs):
        raise RuntimeError("motor caído")

    monkeypatch.setattr(ui_intents, "decided_category", _broken)

    out = await _call(_tool(tmp_path, CATALOG_31), intro_text="Nuestros velones:", category="Velones")

    assert out["queued"] is True and out["category"] == "Velones" and out["count"] == 12
