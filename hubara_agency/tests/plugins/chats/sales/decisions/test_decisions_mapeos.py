"""Mapeos a listas cerradas dentro de las tools (diseño v2 §07, familia B,
fase F3): categoría pedida, familia de color, ítem del pedido y zona de envío.

Cada uno es una capacidad: choice sobre la lista cerrada más «ambiguo» y
«ninguno»; el valor final se valida contra la lista (una opción fuera de ella
no pasa); «ambiguo» o poca certeza dejan la regla de hoy. La tool le pide la
decisión al motor con `guards.decide_for_session` (sesión + vault).

Para cada uno: (a) con `reglas` es la regla de hoy y Jev no se consulta,
(b) con `jev` se corrige un falso positivo/negativo concreto, (c) si Jev
falla o duda decide la regla, (d) en `sombra` decide la regla y el desacuerdo
va a la cola.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.catalog.color_families import parse_color_families
from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.config.shipping import shipping_zone
from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog
from src.plugins.chats.agent.sales.decisions.guards import (
    Categoria,
    CategoriaPedida,
    CiudadDeEnvio,
    ColorPedido,
    DatoDelItem,
    FamiliaDeColor,
    ItemDelPedido,
    ZonaDeEnvio,
    decide_for_session,
)
from src.plugins.chats.agent.sales.use_cases.order_draft import item_for_values
from src.sdk.catalogkit import CatalogCategoryDTO, resolve_category, resolve_color_family
from src.sdk.connectorkit import TypedAnswer

SID = "wa_573001234567"
_MAPEOS = ("categoria", "familia_de_color", "item_del_pedido", "zona_de_envio")


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
def jev(monkeypatch):
    monkeypatch.setenv("DECISIONS_BOT", "B")


@pytest.fixture
def shadow(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    bots.write_capability_modes(tmp_path, {c: "shadow" for c in _MAPEOS})


async def _decide(capability, inp, vault: Path | None):
    return await decide_for_session(capability, inp, session_id=SID, vault_dir=vault)


def _questions(fake: FakePerceptionAdapter) -> list[str]:
    return [q for _, qs in fake.calls for q in qs]


# --- categoría pedida ---------------------------------------------------------

CATEGORIES = (
    CatalogCategoryDTO(slug="velas-aromaticas", label="Velas Aromáticas", product_count=1),
    CatalogCategoryDTO(slug="velas-religiosas", label="Velas Religiosas", product_count=2),
)


def _slug_of(query: str) -> str | None:
    matched = resolve_category(query, list(CATEGORIES)).matched
    return matched.slug if matched else None


@pytest.mark.parametrize(
    "query", ["religiosas", "velas religosas", "zapatos", "velas", "velas de santos", "aromas", "estampas religiosas"]
)
async def test_category_with_rules_is_todays_resolver(tmp_path: Path, oracle, query: str) -> None:
    verdict = await _decide(Categoria(), CategoriaPedida(query=query, categories=CATEGORIES), tmp_path)

    assert (verdict.value, verdict.by) == ({"categoria": _slug_of(query)}, "reglas")
    assert oracle["fake"].calls == []


async def test_jev_maps_a_category_the_resolver_cannot_read(tmp_path: Path, oracle, jev) -> None:
    """Falso negativo: «velas de santos» no se parece a ninguna categoría por
    texto (el resolver queda ambiguo entre las dos «Velas …»)."""
    oracle["fake"] = FakePerceptionAdapter({"categoria.cual": _choice("categoria.cual", "velas_religiosas", 0.93)})

    verdict = await _decide(Categoria(), CategoriaPedida(query="velas de santos", categories=CATEGORIES), tmp_path)

    assert (verdict.rule, verdict.value, verdict.by) == ({"categoria": None}, {"categoria": "velas-religiosas"}, "jev")
    [(state, _)] = oracle["fake"].calls
    assert "velas de santos" in state and "Velas Religiosas" in state


async def test_jev_says_prints_are_no_category_of_candles(tmp_path: Path, oracle, jev) -> None:
    """Falso positivo: «estampas religiosas» pega por la palabra «religiosas»
    y la búsqueda se filtraba a velas como si fueran estampas."""
    oracle["fake"] = FakePerceptionAdapter({"categoria.cual": _choice("categoria.cual", "ninguno", 0.9)})

    verdict = await _decide(Categoria(), CategoriaPedida(query="estampas religiosas", categories=CATEGORIES), tmp_path)

    assert (verdict.rule, verdict.value) == ({"categoria": "velas-religiosas"}, {"categoria": None})


@pytest.mark.parametrize(
    ("answers", "error"),
    [({"categoria.cual": _choice("categoria.cual", "ambiguo", 0.95)}, None),
     ({"categoria.cual": _choice("categoria.cual", "velas_religiosas", 0.6)}, None),
     ({}, "http_503")],
    ids=["ambiguo", "poca-certeza", "falla"],
)
async def test_category_falls_back_to_the_resolver(tmp_path: Path, oracle, jev, answers, error) -> None:
    oracle["fake"] = FakePerceptionAdapter(answers, error=error)

    verdict = await _decide(Categoria(), CategoriaPedida(query="velas de santos", categories=CATEGORIES), tmp_path)

    assert (verdict.value, verdict.by) == ({"categoria": None}, "respaldo")


async def test_without_categories_jev_is_not_asked(tmp_path: Path, oracle, jev) -> None:
    verdict = await _decide(Categoria(), CategoriaPedida(query="religiosas", categories=()), tmp_path)

    assert verdict.value == {"categoria": None} and oracle["fake"].calls == []


async def test_category_in_shadow_queues_the_disagreement(tmp_path: Path, oracle, shadow) -> None:
    oracle["fake"] = FakePerceptionAdapter({"categoria.cual": _choice("categoria.cual", "velas_religiosas", 0.95)})

    verdict = await _decide(Categoria(), CategoriaPedida(query="velas de santos", categories=CATEGORIES), tmp_path)

    assert (verdict.value, verdict.by) == ({"categoria": None}, "reglas")
    [item] = DisagreementLog(tmp_path).pending()
    assert (item["capability"], item["jev"]) == ("categoria", {"categoria": "velas-religiosas"})


# --- familia de color ---------------------------------------------------------

FAMILIES = parse_color_families({
    "version": 1,
    "modifiers": ["claro", "clarito"],
    "families": {
        "rojo": {"label": "Rojo", "shades": ["rojo", "vinotinto", "vino"]},
        "azul": {"label": "Azul", "shades": ["azul", "celeste", "marino"]},
        "amarillo": {"label": "Amarillo", "shades": ["amarillo", "dorado", "oro"]},
    },
})
COLORS = ("Rojo", "Azul", "Amarillo", "Dorado")


def _color(token: str) -> ColorPedido:
    return ColorPedido(color=token, colores=COLORS, familias=FAMILIES, producto="Cubo Love")


def _rule_json(token: str) -> dict:
    res = resolve_color_family(token, list(COLORS), FAMILIES)
    if res is None:
        return {"estado": None, "color": None, "familias": [], "candidatas": [], "tono": False}
    return {"estado": res.status, "color": res.canonical, "familias": list(res.families),
            "candidatas": list(res.candidates), "tono": res.shade_requested}


@pytest.mark.parametrize("token", ["vinotinto", "celeste clarito", "oro", "bordó", "verde", "Azul"])
async def test_color_with_rules_is_todays_family_resolution(tmp_path: Path, oracle, token: str) -> None:
    verdict = await _decide(FamiliaDeColor(), _color(token), tmp_path)

    assert (verdict.value, verdict.by) == (_rule_json(token), "reglas")
    assert oracle["fake"].calls == []


async def test_jev_maps_a_shade_missing_from_the_family_table(tmp_path: Path, oracle, jev) -> None:
    """Falso negativo: «bordó» no está en la tabla de familias y el color se
    rechazaba («ese color no lo manejo») aunque el catálogo tiene Rojo."""
    oracle["fake"] = FakePerceptionAdapter({"color.cual": _choice("color.cual", "rojo", 0.92)})

    verdict = await _decide(FamiliaDeColor(), _color("bordó"), tmp_path)

    assert verdict.rule["estado"] is None
    assert verdict.value == {"estado": "resolved", "color": "Rojo", "familias": ["Rojo"], "candidatas": ["Rojo"],
                             "tono": True}
    [(state, _)] = oracle["fake"].calls
    assert "bordó" in state and "Dorado" in state and "Cubo Love" in state


async def test_jev_settles_a_shade_the_table_leaves_ambiguous(tmp_path: Path, oracle, jev) -> None:
    """«oro» cae en la familia amarillo y el producto tiene Amarillo y Dorado:
    la regla no adivina; Jev sabe que el oro es el dorado."""
    oracle["fake"] = FakePerceptionAdapter({"color.cual": _choice("color.cual", "dorado", 0.9)})

    verdict = await _decide(FamiliaDeColor(), _color("oro"), tmp_path)

    assert verdict.rule["estado"] == "ambiguous"
    assert (verdict.value["estado"], verdict.value["color"], verdict.value["familias"]) == ("resolved", "Dorado", ["Amarillo"])


async def test_when_jev_agrees_there_is_no_color_the_detail_of_today_stays(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({"color.cual": _choice("color.cual", "ninguno", 0.9)})

    verdict = await _decide(FamiliaDeColor(), _color("oro"), tmp_path)

    assert verdict.value == _rule_json("oro")  # ambigua con sus candidatas, como hoy
    assert verdict.by == "jev"


@pytest.mark.parametrize(
    ("answers", "error"),
    [({"color.cual": _choice("color.cual", "ambiguo", 0.9)}, None), ({}, "timeout")], ids=["ambiguo", "falla"]
)
async def test_color_falls_back_to_the_rule(tmp_path: Path, oracle, jev, answers, error) -> None:
    oracle["fake"] = FakePerceptionAdapter(answers, error=error)

    verdict = await _decide(FamiliaDeColor(), _color("bordó"), tmp_path)

    assert (verdict.value, verdict.by) == (_rule_json("bordó"), "respaldo")


async def test_color_in_shadow_queues_the_disagreement(tmp_path: Path, oracle, shadow) -> None:
    oracle["fake"] = FakePerceptionAdapter({"color.cual": _choice("color.cual", "rojo", 0.92)})

    verdict = await _decide(FamiliaDeColor(), _color("bordó"), tmp_path)

    assert (verdict.value, verdict.by) == (_rule_json("bordó"), "reglas")
    [item] = DisagreementLog(tmp_path).pending()
    assert (item["capability"], item["jev"]["color"]) == ("familia_de_color", "Rojo")


# --- ítem del pedido ----------------------------------------------------------

ITEMS = ("Duo Zodiacal", "Velón Amor Eterno")
PARA_EL_VELON = [{"role": "assistant", "content": "¿Qué aroma quieres?"},
                 {"role": "user", "content": "La lavanda es para el velón"}]


def _item(aceptan, *, actual: int = 0, items=ITEMS, events=()) -> DatoDelItem:
    return DatoDelItem(valores={"aroma": "Lavanda"}, items=items, actual=actual, aceptan=tuple(aceptan), events=events)


@pytest.mark.parametrize(
    ("actual", "aceptan"), [(0, (True, True)), (1, (True, True)), (0, (False, True)), (0, (False, False)), (0, (None, True))]
)
async def test_item_with_rules_is_todays_choice(tmp_path: Path, oracle, actual: int, aceptan) -> None:
    verdict = await _decide(ItemDelPedido(), _item(aceptan, actual=actual), tmp_path)

    k = item_for_values(actual, list(aceptan))
    assert (verdict.value, verdict.by) == ({"item": k, "producto": ITEMS[k]}, "reglas")
    assert oracle["fake"].calls == []


async def test_jev_puts_the_aroma_on_the_product_the_customer_named(tmp_path: Path, oracle, jev) -> None:
    """Falso positivo: los dos productos manejan Lavanda y la regla la pone en
    el ítem en curso (el Duo), aunque el cliente dijo que es para el velón."""
    oracle["fake"] = FakePerceptionAdapter({"item.cual": _choice("item.cual", "item_2", 0.92)})

    verdict = await _decide(ItemDelPedido(), _item((True, True), events=PARA_EL_VELON), tmp_path)

    assert (verdict.rule["item"], verdict.value, verdict.by) == (0, {"item": 1, "producto": "Velón Amor Eterno"}, "jev")
    [(state, _)] = oracle["fake"].calls
    assert "para el velón" in state and "Lavanda" in state


@pytest.mark.parametrize("aceptan", [(True, False), (False, False), (False, True)])
async def test_jev_only_chooses_among_items_that_take_the_value(tmp_path: Path, oracle, jev, aceptan) -> None:
    """Con menos de dos ítems que acepten el valor, lo resuelve el catálogo."""
    verdict = await _decide(ItemDelPedido(), _item(aceptan, events=PARA_EL_VELON), tmp_path)

    assert verdict.value["item"] == item_for_values(0, list(aceptan))
    assert oracle["fake"].calls == []


@pytest.mark.parametrize(
    ("answers", "error"),
    [({"item.cual": _choice("item.cual", "ninguno", 0.9)}, None), ({}, "http_503")], ids=["ninguno", "falla"]
)
async def test_item_falls_back_to_the_rule(tmp_path: Path, oracle, jev, answers, error) -> None:
    oracle["fake"] = FakePerceptionAdapter(answers, error=error)

    verdict = await _decide(ItemDelPedido(), _item((True, True), events=PARA_EL_VELON), tmp_path)

    assert (verdict.value, verdict.by) == ({"item": 0, "producto": "Duo Zodiacal"}, "respaldo")


async def test_item_in_shadow_queues_the_disagreement(tmp_path: Path, oracle, shadow) -> None:
    oracle["fake"] = FakePerceptionAdapter({"item.cual": _choice("item.cual", "item_2", 0.95)})

    verdict = await _decide(ItemDelPedido(), _item((True, True), events=PARA_EL_VELON), tmp_path)

    assert (verdict.value["item"], verdict.by) == (0, "reglas")
    [item] = DisagreementLog(tmp_path).pending()
    assert (item["capability"], item["jev"]["item"]) == ("item_del_pedido", 1)


# --- zona de envío ------------------------------------------------------------


@pytest.mark.parametrize("city", ["Bogotá", "bogota d.c.", "Chía", "Medellín", "", None])
async def test_zone_with_rules_is_todays_zone(tmp_path: Path, oracle, city) -> None:
    verdict = await _decide(ZonaDeEnvio(), CiudadDeEnvio(ciudad=city), tmp_path)

    assert (verdict.value, verdict.by) == ({"zona": shipping_zone(city)}, "reglas")
    assert oracle["fake"].calls == []


@pytest.mark.parametrize(("city", "pick", "zone"), [("Chía", "bogota", "bogota"), ("Medellín", "nacional", "nacional")])
async def test_jev_knows_the_zone_of_a_city(tmp_path: Path, oracle, jev, city, pick, zone) -> None:
    """Falso negativo: sin lista de municipios cercanos, fuera de Bogotá hoy
    valen las dos tarifas (Chía pagaba la nacional; Medellín, la de Bogotá)."""
    oracle["fake"] = FakePerceptionAdapter({"zona.cual": _choice("zona.cual", pick, 0.93)})

    verdict = await _decide(ZonaDeEnvio(), CiudadDeEnvio(ciudad=city), tmp_path)

    assert (verdict.rule, verdict.value, verdict.by) == ({"zona": None}, {"zona": zone}, "jev")


async def test_without_a_city_jev_is_not_asked(tmp_path: Path, oracle, jev) -> None:
    verdict = await _decide(ZonaDeEnvio(), CiudadDeEnvio(ciudad="  "), tmp_path)

    assert verdict.value == {"zona": None} and oracle["fake"].calls == []


@pytest.mark.parametrize(
    ("answers", "error"),
    [({"zona.cual": _choice("zona.cual", "ambiguo", 0.9)}, None),
     ({"zona.cual": _choice("zona.cual", "bogota", 0.7)}, None), ({}, "timeout")],
    ids=["ambiguo", "poca-certeza", "falla"],
)
async def test_zone_falls_back_to_the_rule(tmp_path: Path, oracle, jev, answers, error) -> None:
    oracle["fake"] = FakePerceptionAdapter(answers, error=error)

    verdict = await _decide(ZonaDeEnvio(), CiudadDeEnvio(ciudad="Chía"), tmp_path)

    assert (verdict.value, verdict.by) == ({"zona": None}, "respaldo")


async def test_zone_in_shadow_queues_the_disagreement(tmp_path: Path, oracle, shadow) -> None:
    oracle["fake"] = FakePerceptionAdapter({"zona.cual": _choice("zona.cual", "bogota", 0.95)})

    verdict = await _decide(ZonaDeEnvio(), CiudadDeEnvio(ciudad="Chía"), tmp_path)

    assert (verdict.value, verdict.by) == ({"zona": None}, "reglas")
    [item] = DisagreementLog(tmp_path).pending()
    assert (item["capability"], item["rule"], item["jev"]) == ("zona_de_envio", {"zona": None}, {"zona": "bogota"})


# --- la guardia sin vault ------------------------------------------------------


async def test_a_tool_without_vault_gets_todays_rule(oracle, monkeypatch) -> None:
    """Sin vault (una tool armada sin él) no hay control del despliegue ni
    cola: decide la regla de hoy."""
    monkeypatch.delenv("DECISIONS_BOT", raising=False)

    verdict = await _decide(ZonaDeEnvio(), CiudadDeEnvio(ciudad="Chía"), None)

    assert (verdict.value, verdict.by) == ({"zona": None}, "reglas")


async def test_without_vault_the_lab_bot_pinned_for_the_case_still_decides(oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({"zona.cual": _choice("zona.cual", "bogota", 0.95)})

    verdict = await _decide(ZonaDeEnvio(), CiudadDeEnvio(ciudad="Chía"), None)

    assert (verdict.value, verdict.by) == ({"zona": "bogota"}, "jev")


def test_the_mappings_wait_for_jev_like_every_capability() -> None:
    """Sin tope propio (antes 2 s): la espera es la del perfil del oráculo."""
    assert not any(hasattr(c, "timeout_s") for c in (Categoria, FamiliaDeColor, ItemDelPedido, ZonaDeEnvio))


# ── producto nombrado (remarketing): qué ficha ve el gancho ──


def test_producto_nombrado_adds_the_product_named_in_other_words_and_keeps_the_exact_ones() -> None:
    from src.plugins.chats.agent.sales.decisions.capabilities.mapeos import PRODUCTO_NOMBRADO, ProductosDeLaCharla
    from src.sdk.connectorkit import PerceptionResult, TypedAnswer

    inp = ProductosDeLaCharla(text="el Cubo Love y la de los corazoncitos", titles=("Cubo Love", "Cubo de corazón", "Velón Koala"),
                              named=("Cubo Love",))
    state, [question] = PRODUCTO_NOMBRADO.ask(inp)
    pick = TypedAnswer(id="producto.nombrado", kind="choice", choice="cubo_de_corazon", probs=(("cubo_de_corazon", 0.92),),
                       confidence=0.92)
    result = PerceptionResult(ok=True, answers=(pick,), provider="fake", model="typesafe/jev-1.13-x")

    assert set(question.options) == {"cubo_love", "cubo_de_corazon", "velon_koala", "ambiguo", "ninguno"}
    assert "Cubo de corazón" in state
    assert PRODUCTO_NOMBRADO.rule(inp) == ("Cubo Love",)
    jev = PRODUCTO_NOMBRADO.decide(inp, result, PRODUCTO_NOMBRADO.rule(inp), {})
    assert jev == ("Cubo de corazón",)
    assert set(PRODUCTO_NOMBRADO.floor(inp, PRODUCTO_NOMBRADO.rule(inp), jev)) == {"Cubo Love", "Cubo de corazón"}
