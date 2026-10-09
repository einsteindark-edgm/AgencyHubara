"""Las tools le piden los mapeos al motor (fase F3) por su guardia
(`guards.decide_for_session`) y aplican el valor con el código de siempre:

* `search_products`: la categoría pedida (si el motor decide otra categoría
  de la lista cerrada, o ninguna, se aplica esa);
* `set_order_slot`: la familia de un color que no está tal cual en el
  catálogo y a cuál producto del pedido va un dato que llega sin producto;
* `present_order_confirmation` y `register_order`: la zona de envío de la
  ciudad (la tarifa la sigue validando el código).

Con `reglas` (así nace todo) cada tool responde lo de hoy. Acá Jev decide con
el bot `B` fijado (como el laboratorio) y un oráculo falso.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.color_families_loader import DEFAULT_COLOR_FAMILIES_PATH, ColorFamiliesLoader
from src.platform.catalog.dtos import CatalogManifestDTO, CatalogProductDTO, SearchResult
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.config.shipping import SHIPPING_RATE_BOGOTA_COP, SHIPPING_RATE_NATIONAL_COP
from src.plugins.chats.agent.sales.tools.catalog import SearchProductsTool
from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool
from src.plugins.chats.agent.sales.tools.ui_intents import PresentOrderConfirmationTool
from src.plugins.chats.shared.draft_items import draft_items
from src.sdk.connectorkit import TypedAnswer
from tests.plugins.chats.sales.test_catalog_category_filter import _product as _snapshot_product
from tests.plugins.chats.sales.test_shipping_value_rules import _CapturingPort, _OneProductCatalog, _register


def _choice(qid: str, pick: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="choice", choice=pick, probs=((pick, p),), confidence=p)


@pytest.fixture
def oracle(monkeypatch):
    from src.sdk import connectorkit

    holder = {"fake": FakePerceptionAdapter({})}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


@pytest.fixture
def rules(monkeypatch):
    monkeypatch.delenv("DECISIONS_BOT", raising=False)


def _jev(monkeypatch) -> None:
    monkeypatch.setenv("DECISIONS_BOT", "B")


# --- search_products: categoría pedida ---------------------------------------


@pytest.fixture
def snapshot(tmp_path: Path) -> LocalSnapshotCatalogClient:
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "snapshot.json").write_text(json.dumps([
        _snapshot_product("1", "corona", "Corona de Redención", "velas-religiosas", "Velas Religiosas"),
        _snapshot_product("2", "cruz-de-vida", "Cruz de Vida", "velas-religiosas", "Velas Religiosas"),
        _snapshot_product("3", "luz-serena", "Luz Serena", "velas-aromaticas", "Velas Aromáticas"),
    ]), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(
        {"version": "v1", "fetched_at": "2099-01-01T00:00:00+00:00", "product_count": 3}), encoding="utf-8")
    return LocalSnapshotCatalogClient(root)


def _search_ctx() -> ToolContext:
    return ToolContext(session_key="wa_573001234567", channel="whatsapp", chat_id="c")


async def _search(catalog, category: str) -> dict:
    return json.loads(await SearchProductsTool(workspace=".", catalog=catalog).execute_with_context(
        _search_ctx(), q="", category=category))


async def test_search_with_rules_leaves_saints_candles_ambiguous_as_today(snapshot, oracle, rules) -> None:
    payload = await _search(snapshot, "velas de santos")

    assert payload["results"] == [] and payload["category"]["matched"] is None
    assert payload["category"]["candidates"] == ["Velas Aromáticas", "Velas Religiosas"]
    assert oracle["fake"].calls == []


async def test_search_filters_the_category_jev_maps(snapshot, oracle, monkeypatch) -> None:
    _jev(monkeypatch)
    oracle["fake"] = FakePerceptionAdapter({"categoria.cual": _choice("categoria.cual", "velas_religiosas", 0.93)})

    payload = await _search(snapshot, "velas de santos")

    assert [r["handle"] for r in payload["results"]] == ["corona", "cruz-de-vida"]
    assert payload["category"]["matched"] == "Velas Religiosas"
    assert payload["category"]["query"] == "velas de santos"


async def test_search_drops_a_category_jev_says_does_not_exist(snapshot, oracle, monkeypatch) -> None:
    _jev(monkeypatch)
    oracle["fake"] = FakePerceptionAdapter({"categoria.cual": _choice("categoria.cual", "ninguno", 0.9)})

    payload = await _search(snapshot, "estampas religiosas")

    assert payload["results"] == [] and payload["category"]["matched"] is None
    assert payload["category"]["available"] == ["Velas Aromáticas", "Velas Religiosas"]


async def test_search_keeps_todays_category_when_jev_fails(snapshot, oracle, monkeypatch) -> None:
    _jev(monkeypatch)
    oracle["fake"] = FakePerceptionAdapter({}, error="timeout")

    payload = await _search(snapshot, "religiosas")

    assert [r["handle"] for r in payload["results"]] == ["corona", "cruz-de-vida"]
    assert payload["category"]["matched"] == "Velas Religiosas"


# --- set_order_slot: familia de color e ítem del pedido ------------------------

FAM = ColorFamiliesLoader(DEFAULT_COLOR_FAMILIES_PATH).load()
_CUBO = CatalogProductDTO(id="prod_cubo", handle="cubo-love", title="Cubo Love", status="published",
                          tags=["Aroma: Lavanda", "Aroma: Coco", "Color: Rojo", "Color: Azul"])
_DUO = CatalogProductDTO(id="prod_duo", handle="duo-zodiacal", title="Duo Zodiacal", status="published",
                         tags=["Aroma: Frutos rojos", "Aroma: Lavanda", "Color: Blanco", "Color: Negro"],
                         options={"Signo": ["Aries", "Leo", "Escorpio"]})
_VELON = CatalogProductDTO(id="prod_velon", handle="velon-amor-eterno", title="Velón Amor Eterno",
                           status="published", tags=["Aroma: Lavanda", "Aroma: Coco", "Color: Blanco", "Color: Negro"],
                           options={"Unico": ["Unico"]})


class _Catalog:
    async def search(self, q: str, *, limit: int = 10) -> SearchResult:
        return SearchResult(
            query=q, count=3, truncated=False, stale=False,
            manifest=CatalogManifestDTO(version="v1", fetched_at="2026-09-28T00:00:00Z", product_count=3),
            results=[_CUBO, _DUO, _VELON],
        )


SLOT_SID = "wa_slots"


def _slot_ctx() -> ToolContext:
    return ToolContext(session_key=SLOT_SID, channel="whatsapp", chat_id="c")


def _slot_tool(tmp_path: Path, events: list[dict] | None = None) -> SetOrderSlotTool:
    return SetOrderSlotTool(
        workspace=tmp_path, vault_dir=tmp_path / "vault", catalog=_Catalog(), color_families=FAM,
        history_reader=lambda _sid: list(events or []),
    )


async def _set(tool: SetOrderSlotTool, **kwargs) -> dict:
    return json.loads(await tool.execute_with_context(_slot_ctx(), **kwargs))


def _items(tmp_path: Path) -> list[dict]:
    meta = json.loads((tmp_path / "vault" / SLOT_SID / "metadata.json").read_text(encoding="utf-8"))
    return draft_items(meta["episodes"][-1]["order_draft"])


async def test_set_order_slot_with_rules_rejects_a_shade_missing_from_the_table(tmp_path, oracle, rules) -> None:
    out = await _set(_slot_tool(tmp_path), producto="Cubo Love", color="bordó")

    assert out["rejected"][0]["invalid"] == ["bordó"]
    assert oracle["fake"].calls == []


async def test_set_order_slot_captures_the_color_jev_maps(tmp_path, oracle, monkeypatch) -> None:
    _jev(monkeypatch)
    oracle["fake"] = FakePerceptionAdapter({"color.cual": _choice("color.cual", "rojo", 0.92)})

    out = await _set(_slot_tool(tmp_path), producto="Cubo Love", color="bordó")

    assert "rejected" not in out
    assert out["captured"]["color"] == "Rojo"
    assert out["color_family"] == {"requested": "bordó", "captured": "Rojo", "family": "Rojo", "shade_requested": True}
    assert "bordó" in out["order_draft"]["notas"]  # el tono pedido queda para el operador


async def test_an_exact_catalog_color_never_asks_jev(tmp_path, oracle, monkeypatch) -> None:
    _jev(monkeypatch)

    out = await _set(_slot_tool(tmp_path), producto="Cubo Love", color="azul")

    assert out["captured"]["color"] == "Azul"
    assert oracle["fake"].calls == []


async def _velon_then_duo(tmp_path: Path, events: list[dict]) -> SetOrderSlotTool:
    """Pedido con el Velón y el Duo; el ítem en curso es el Duo (el último)."""
    tool = _slot_tool(tmp_path, events)
    await _set(tool, producto="Velón Amor Eterno")
    await _set(tool, producto="Duo Zodiacal", diseno="Leo")
    return tool


PARA_EL_VELON = [{"role": "assistant", "content": "¿Qué aroma quieres para cada una?"},
                 {"role": "user", "content": "La lavanda es para el velón"}]


async def test_with_rules_a_value_without_product_goes_to_the_item_in_progress(tmp_path, oracle, rules) -> None:
    tool = await _velon_then_duo(tmp_path, PARA_EL_VELON)

    await _set(tool, aroma="Lavanda")

    by_product = {i["producto"]: i for i in _items(tmp_path)}
    assert by_product["Duo Zodiacal"].get("aroma") == "Lavanda"
    assert "aroma" not in by_product["Velón Amor Eterno"]


async def test_jev_puts_the_value_on_the_product_the_customer_named(tmp_path, oracle, monkeypatch) -> None:
    tool = await _velon_then_duo(tmp_path, PARA_EL_VELON)
    _jev(monkeypatch)
    oracle["fake"] = FakePerceptionAdapter({"item.cual": _choice("item.cual", "item_1", 0.93)})

    out = await _set(tool, aroma="Lavanda")

    by_product = {i["producto"]: i for i in _items(tmp_path)}
    assert by_product["Velón Amor Eterno"].get("aroma") == "Lavanda"
    assert "aroma" not in by_product["Duo Zodiacal"]
    assert out["captured"]["producto"] == "Velón Amor Eterno"
    [(state, _)] = [call for call in oracle["fake"].calls if call[1] == ("item.cual",)]
    assert "para el velón" in state


async def test_a_value_only_one_product_takes_goes_there_without_asking(tmp_path, oracle, monkeypatch) -> None:
    """«Escorpio» solo es del Duo: lo resuelve el catálogo, no Jev."""
    tool = _slot_tool(tmp_path)
    await _set(tool, producto="Duo Zodiacal")
    await _set(tool, producto="Velón Amor Eterno")
    _jev(monkeypatch)

    await _set(tool, diseno="Escorpio")

    by_product = {i["producto"]: i for i in _items(tmp_path)}
    assert by_product["Duo Zodiacal"].get("diseno") == "Escorpio"
    assert not any(call[1] == ("item.cual",) for call in oracle["fake"].calls)


# --- zona de envío -------------------------------------------------------------


def _seed_city(vault: Path, city: str) -> None:
    md = vault / "s_ship" / "metadata.json"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(json.dumps({"episodes": [{
        "episode_id": "ep_1", "started_at_ms": 1, "closed_at_ms": None,
        "order_draft": {"slots": {"ciudad": city}},
    }]}), encoding="utf-8")


async def _confirm(tmp_path: Path, shipping_cop: int) -> dict:
    tool = PresentOrderConfirmationTool(workspace=str(tmp_path), catalog=_OneProductCatalog())
    return json.loads(await tool.execute_with_context(
        ToolContext(session_key="s_ship", channel="whatsapp", chat_id="c"),
        items=[{"handle": "velon-amor-eterno", "quantity": 1, "unit_price_cop": 38500}],
        shipping_cop=shipping_cop, shipping_address_summary="Calle 1 # 2-3, Chía", payment_method="transfer",
    ))


async def test_with_rules_chia_can_pay_the_national_rate_as_today(tmp_path, _isolate_vault_dir, oracle, rules) -> None:
    _seed_city(_isolate_vault_dir, "Chía")

    result = await _confirm(tmp_path, SHIPPING_RATE_NATIONAL_COP)

    assert result["queued"] is True


async def test_jev_knows_chia_pays_the_bogota_rate(tmp_path, _isolate_vault_dir, oracle, monkeypatch) -> None:
    _seed_city(_isolate_vault_dir, "Chía")
    _jev(monkeypatch)
    oracle["fake"] = FakePerceptionAdapter({"zona.cual": _choice("zona.cual", "bogota", 0.94)})

    result = await _confirm(tmp_path, SHIPPING_RATE_NATIONAL_COP)

    assert (result["queued"], result.get("error")) == (False, "shipping_mismatch")
    assert "no es la tarifa publicada para Chía" in result["message"]


async def test_register_order_with_rules_takes_the_bogota_rate_for_medellin(_isolate_vault_dir, oracle, rules) -> None:
    port = _CapturingPort()

    env = await _register(_isolate_vault_dir, port, city="Medellín", shipping_cop=SHIPPING_RATE_BOGOTA_COP)

    assert env["registered"] is True, env


async def test_jev_knows_medellin_pays_the_national_rate(_isolate_vault_dir, oracle, monkeypatch) -> None:
    _jev(monkeypatch)
    oracle["fake"] = FakePerceptionAdapter({"zona.cual": _choice("zona.cual", "nacional", 0.95)})
    port = _CapturingPort()

    env = await _register(_isolate_vault_dir, port, city="Medellín", shipping_cop=SHIPPING_RATE_BOGOTA_COP)

    assert (env["registered"], env.get("error_detail")) == (False, "shipping_mismatch")
    assert port.calls == []


# --- el worker les da a las tools lo que necesitan -----------------------------


def test_the_sales_worker_gives_the_tools_the_vault_and_the_history(_isolate_vault_dir, monkeypatch) -> None:
    """Sin el vault, `search_products` no ve el control del despliegue ni la
    cola; sin el historial, Jev no sabe a qué producto se refiere el cliente."""
    monkeypatch.setenv("MEDUSA_BASE_URL", "http://medusa.test")
    monkeypatch.setenv("MEDUSA_ADMIN_TOKEN", "dummy")
    import src.plugins.chats.workers.sales  # noqa: F401  (registra las tools)
    from src.platform.tool_extensions import _EXTENSIONS  # type: ignore

    search = dict(_EXTENSIONS)["sales.search_products"](_isolate_vault_dir)
    slots = dict(_EXTENSIONS)["sales.set_order_slot"](_isolate_vault_dir)

    assert getattr(search, "_vault_dir", None) == _isolate_vault_dir
    assert getattr(slots, "_history_reader", None) is not None
