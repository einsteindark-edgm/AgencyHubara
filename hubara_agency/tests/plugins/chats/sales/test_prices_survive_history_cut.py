"""Los precios de lo que el bot buscó y mostró sobreviven el corte del historial.

Incidente 2026-10-25/26 (bot V2, anuncio de velas navideñas): el cliente
preguntó «Motivos y precios»; el bot buscó «navidad» y le mostró la lista de
los cuatro navideños ($47.000, $49.000, $53.000 y $54.000). El turno de
complemento («el cliente también preguntó por precio») es otra llamada al
modelo y lee el historial guardado, donde la librería de la conversación corta
cada resultado de tool a 500 caracteres: de `search_products` quedaba solo el
primer producto y `present_products` no traía precios. El bot escribió «Los
precios van desde $47.000 hasta $89.900» (y, en otra conversación, $89.000).

Ahora el rango y el precio de cada producto van al principio del resultado:
el corte es el de la librería real, no una cifra copiada.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolContext
from exoclaw_conversation.conversation import _TOOL_RESULT_MAX_CHARS

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.plugins.chats.agent.sales.tools.catalog import SearchProductsTool
from src.plugins.chats.agent.sales.tools.ui_intents import PresentProductsTool

SESSION = "wa_573001234567"
ASSETS = "https://assets.example.test/1.%20hero-desktop_2560x1440%20(4)-01M52R5KK8S60KN63V6E9FF669"


def _raw(handle: str, title: str, price: int) -> dict[str, Any]:
    """Un producto con la forma del snapshot de producción: foto, galería y
    etiquetas largas (lo que hacía que solo cupiera el primero)."""
    return {
        "id": f"prod_{handle}",
        "handle": handle,
        "title": title,
        "status": "published",
        "thumbnail": f"{ASSETS}-{handle}.webp",
        "images": [{"url": f"{ASSETS}-{handle}-{n}.webp", "rank": n} for n in range(3)],
        "tags": ["Aroma: Navidad"],
        "variants": [
            {
                "id": f"variant_{handle}",
                "title": "Unico",
                "sku": f"HUB-{handle.upper()}",
                "prices": [{"amount": str(price), "currency_code": "cop"}],
            }
        ],
        "categories": ["decorativas"],
        "category_labels": {"decorativas": "Decorativas"},
    }


NAVIDAD = [
    _raw("papa-noel-y-sus-regalos", "Papá Noel y sus Regalos", 47_000),
    _raw("trio-de-munecos-de-nieve", "Trío de Muñecos de Nieve", 49_000),
    _raw("duo-de-cascanueces", "Dúo de Cascanueces", 54_000),
    _raw("trio-de-gnomos", "Trío de Gnomos", 53_000),
    _raw("velas-de-deseos-onduladas", "Caja de Velas de deseos onduladas navidad", 22_500),
    _raw("velas-de-deseos-cuadradas", "Caja de Velas de deseos cuadradas navidad", 25_000),
]


def _catalog(tmp_path: Path, products: list[dict[str, Any]] = NAVIDAD) -> LocalSnapshotCatalogClient:
    snap = tmp_path / "catalog"
    snap.mkdir()
    (snap / "snapshot.json").write_text(json.dumps(products), encoding="utf-8")
    (snap / "manifest.json").write_text(
        json.dumps({
            "version": "v-test",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "product_count": len(products),
        }),
        encoding="utf-8",
    )
    return LocalSnapshotCatalogClient(snap)


def _ctx() -> ToolContext:
    return ToolContext(session_key=SESSION, channel="whatsapp", chat_id="c")


def _kept(result: str) -> str:
    """Lo que queda del resultado en el historial que leen los turnos siguientes."""
    return result[:_TOOL_RESULT_MAX_CHARS]


async def test_search_products_keeps_every_price_within_the_history_cut(tmp_path: Path) -> None:
    tool = SearchProductsTool(workspace=tmp_path, catalog=_catalog(tmp_path))

    result = await tool.execute_with_context(_ctx(), q="navidad", limit=20)

    assert json.loads(result)["count"] == 6
    kept = _kept(result)
    assert "de $22.500 a $54.000" in kept
    for line in ("Papá Noel y sus Regalos $47.000", "Trío de Muñecos de Nieve $49.000",
                 "Dúo de Cascanueces $54.000", "Trío de Gnomos $53.000"):
        assert line in kept


SHOWN = ["papa-noel-y-sus-regalos", "trio-de-munecos-de-nieve", "duo-de-cascanueces", "trio-de-gnomos"]
SHOWN_LINES = ("Papá Noel y sus Regalos $47.000", "Trío de Muñecos de Nieve $49.000",
               "Dúo de Cascanueces $54.000", "Trío de Gnomos $53.000")


async def test_present_products_keeps_the_prices_of_what_it_showed_within_the_history_cut(tmp_path: Path) -> None:
    """El turno del incidente: la lista de los cuatro navideños."""
    tool = PresentProductsTool(workspace=str(tmp_path), catalog=_catalog(tmp_path))

    result = await tool.execute_with_context(_ctx(), handles=SHOWN, intro_text="Estos son los motivos navideños.")

    assert json.loads(result)["count"] == 4
    kept = _kept(result)
    assert "de $47.000 a $54.000" in kept
    for line in SHOWN_LINES:
        assert line in kept
    # Lo que no se mostró no entra al rango (las cajas de velas de deseos).
    assert "$22.500" not in kept


async def test_the_whole_catalog_keeps_its_prices_within_the_history_cut(tmp_path: Path) -> None:
    tool = PresentProductsTool(workspace=str(tmp_path), catalog=_catalog(tmp_path))

    result = await tool.execute_with_context(_ctx(), intro_text="Este es nuestro catálogo.")

    assert json.loads(result)["count"] == 6
    kept = _kept(result)
    assert "de $22.500 a $54.000" in kept
    for line in SHOWN_LINES:
        assert line in kept


async def test_a_category_keeps_its_prices_within_the_history_cut(tmp_path: Path) -> None:
    tool = PresentProductsTool(workspace=str(tmp_path), catalog=_catalog(tmp_path))

    result = await tool.execute_with_context(_ctx(), category="Decorativas", intro_text="Las decorativas.")

    assert json.loads(result)["category"] == "Decorativas"
    kept = _kept(result)
    assert "de $22.500 a $54.000" in kept
    for line in SHOWN_LINES:
        assert line in kept


async def test_with_a_list_longer_than_the_cut_the_range_still_survives(tmp_path: Path) -> None:
    """Treinta productos no caben en 500 caracteres: el rango va primero."""
    many = [_raw(f"velon-{n:02d}", f"Velón artesanal número {n:02d}", 20_000 + 1_000 * n) for n in range(30)]
    tool = SearchProductsTool(workspace=tmp_path, catalog=_catalog(tmp_path, many))

    result = await tool.execute_with_context(_ctx(), q="velón", limit=30)

    assert json.loads(result)["count"] == 30
    assert "de $20.000 a $49.000" in _kept(result)
