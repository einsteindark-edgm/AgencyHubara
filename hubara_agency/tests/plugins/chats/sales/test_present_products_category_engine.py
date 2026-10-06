"""La categoría que escribe el cliente la decide el motor, también en
`present_products(category=…)` (revisión del PR #394, gotcha 14).

`search_products(category=…)` le pregunta a la capacidad `categoria` del motor
de decisiones (con su interruptor); `present_products(category=…)` resolvía
directo con la regla. Es el camino principal de lo que escribe el cliente
(el menú y la descripción le dicen al LLM que pase el texto tal cual): con el
motor decidiendo «religiosas» para «velas de santos», `search_products`
devolvía las religiosas y `present_products` decía `category_not_found`.

Solo la fila del menú (`categoria:<slug>`, el id que armó el código) se salta
el motor: ese id ya dice cuál es.

Igual que `test_decisions_tools_mapeos.py`: Jev decide con el bot `B` fijado
(como el laboratorio) y un oráculo falso; con `reglas`, la regla de hoy.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.tools.catalog import SearchProductsTool
from src.plugins.chats.agent.sales.tools.ui_intents import PresentProductsTool
from src.sdk.connectorkit import TypedAnswer

SESSION = "wa_573001234567"


def _raw(handle: str, slug: str, label: str) -> dict:
    return {
        "id": f"prod_{handle}",
        "handle": handle,
        "title": handle.replace("-", " ").title(),
        "status": "published",
        "thumbnail": f"https://img.test/{handle}.webp",
        "variants": [{
            "id": f"variant_{handle}",
            "title": "Unico",
            "sku": f"HUB-{handle.upper()}",
            "prices": [{"amount": "23000", "currency_code": "cop"}],
        }],
        "categories": [slug],
        "category_labels": {slug: label},
    }


@pytest.fixture
def catalog(tmp_path: Path) -> LocalSnapshotCatalogClient:
    """31 productos en 3 categorías (no cabe: el cliente ve el menú primero)."""
    root = tmp_path / "snapshot"
    root.mkdir()
    products = (
        [_raw(f"religiosa-{i:02d}", "velas-religiosas", "Velas Religiosas") for i in range(12)]
        + [_raw(f"aromatica-{i:02d}", "velas-aromaticas", "Velas Aromáticas") for i in range(11)]
        + [_raw(f"velon-{i:02d}", "velones", "Velones") for i in range(8)]
    )
    (root / "snapshot.json").write_text(json.dumps(products), encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"version": "v1", "fetched_at": "2099-01-01T00:00:00+00:00", "product_count": 31}),
        encoding="utf-8",
    )
    return LocalSnapshotCatalogClient(root)


@pytest.fixture
def oracle(monkeypatch):
    from src.sdk import connectorkit

    holder = {"fake": FakePerceptionAdapter({})}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


def _jev_maps(oracle, monkeypatch, pick: str) -> None:
    monkeypatch.setenv("DECISIONS_BOT", "B")
    oracle["fake"] = FakePerceptionAdapter({
        "categoria.cual": TypedAnswer(
            id="categoria.cual", kind="choice", choice=pick, probs=((pick, 0.93),), confidence=0.93
        ),
    })


def _ctx() -> ToolContext:
    return ToolContext(session_key=SESSION, channel="whatsapp", chat_id="c")


async def _present(catalog, **kwargs) -> dict:
    tool = PresentProductsTool(workspace=".", catalog=catalog)
    return json.loads(await tool.execute_with_context(_ctx(), intro_text="Estas son:", **kwargs))


def _shown(vault: Path) -> list[str]:
    data = json.loads((vault / SESSION / "metadata.json").read_text(encoding="utf-8"))
    [intent] = data["pending_ui_intents"]
    return [r["id"] for s in intent["params"]["sections"] for r in s["rows"]]


async def test_the_category_the_customer_writes_is_decided_by_the_engine_like_search_products(
    catalog, oracle, monkeypatch, _isolate_vault_dir: Path
) -> None:
    """El caso del revisor: «velas de santos» son las religiosas para el motor."""
    _jev_maps(oracle, monkeypatch, "velas_religiosas")

    searched = json.loads(await SearchProductsTool(workspace=".", catalog=catalog).execute_with_context(
        _ctx(), q="", limit=30, category="velas de santos"))
    out = await _present(catalog, category="velas de santos")

    assert out["queued"] is True and out["category"] == "Velas Religiosas"
    assert _shown(_isolate_vault_dir) == [r["handle"] for r in searched["results"]]
    assert len(_shown(_isolate_vault_dir)) == 12


async def test_with_todays_rule_an_ambiguous_category_shows_nothing_and_offers_the_candidates(
    catalog, oracle, monkeypatch, _isolate_vault_dir: Path
) -> None:
    monkeypatch.delenv("DECISIONS_BOT", raising=False)

    out = await _present(catalog, category="velas de santos")

    assert out["queued"] is False and out["error"] == "category_ambiguous"
    assert out["candidates"] == ["Velas Aromáticas", "Velas Religiosas"]
    assert not (_isolate_vault_dir / SESSION / "metadata.json").exists()
    assert oracle["fake"].calls == []


async def test_a_row_of_the_menu_skips_the_engine_its_id_already_says_which(
    catalog, oracle, monkeypatch, _isolate_vault_dir: Path
) -> None:
    monkeypatch.setenv("DECISIONS_BOT", "B")  # Jev encendido, pero no se le pregunta

    out = await _present(catalog, category="categoria:velones")

    assert out["queued"] is True and out["category"] == "Velones" and out["count"] == 8
    assert oracle["fake"].calls == []
