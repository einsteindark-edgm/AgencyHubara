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

Todo determinístico: el id de la fila decide, nunca el texto del cliente.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.plugins.chats.agent.sales.parsers import parse_whatsapp_inbound
from src.plugins.chats.agent.sales.tools.ui_intents import PresentProductsTool
from src.plugins.chats.agent.sales.translate import translate_to_effective_text

SESSION = "wa_573001234567"


def _raw(handle: str, slug: str | None, label: str | None) -> dict[str, Any]:
    return {
        "id": f"prod_{handle}",
        "handle": handle,
        "title": handle.replace("-", " ").title(),
        "status": "published",
        "variants": [
            {
                "id": f"variant_{handle}",
                "title": "Unico",
                "sku": f"HUB-{handle.upper()}",
                "prices": [{"amount": "23000", "currency_code": "cop"}],
            }
        ],
        "categories": [slug] if slug else [],
        "category_labels": {slug: label} if slug and label else None,
    }


def _group(n: int, slug: str, label: str) -> list[dict[str, Any]]:
    return [_raw(f"{slug}-{i:02d}", slug, label) for i in range(n)]


def _tool(tmp_path: Path, raw_products: list[dict[str, Any]]) -> PresentProductsTool:
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
    return PresentProductsTool(workspace=str(tmp_path), catalog=LocalSnapshotCatalogClient(snap))


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
    assert [c["category"] for c in out["categories"]] == ["aromaticas", "religiosas", "velones"]
    assert "present_products(category=" in out["summary"]
    assert "[el cliente eligió la categoría:" in out["summary"]


@pytest.mark.asyncio
async def test_choosing_a_category_reaches_the_llm_clearly_and_shows_its_products(
    tmp_path: Path, _isolate_vault_dir: Path
):
    tool = _tool(tmp_path, CATALOG_31)
    await _call(tool, intro_text="Con gusto te muestro el catálogo.")
    velones = next(r for r in _rows(_intents(_isolate_vault_dir)[0]) if r["title"] == "Velones")

    # 1) El cliente toca la fila «Velones»: el LLM la recibe como categoría.
    msg = parse_whatsapp_inbound(_list_reply(velones["id"], velones["title"]))
    effective = await translate_to_effective_text(msg)
    assert effective.text == '[el cliente eligió la categoría: Velones (category="velones")]'
    assert effective.structured_payload["id"] == "categoria:velones"

    # 2) El LLM le pide a la tool los productos de esa categoría.
    out = await _call(tool, intro_text="Nuestros velones:", category="velones")

    intents = _intents(_isolate_vault_dir)
    assert len(intents) == 2
    shown = intents[1]
    assert shown["kind"] == "products_list"
    assert [r["id"] for r in _rows(shown)] == [p["handle"] for p in CATALOG_31[:12]]
    assert [s["title"] for s in shown["params"]["sections"]] == ["Velones"]
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
    msg = parse_whatsapp_inbound(_list_reply(menu[-1]["id"], menu[-1]["title"]))
    effective = await translate_to_effective_text(msg)
    category = effective.text.split('category="')[1].rstrip('")]')
    out = await _call(tool, intro_text="Estos son:", category=category)

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
