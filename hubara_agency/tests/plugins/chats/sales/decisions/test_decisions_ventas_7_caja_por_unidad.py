"""`caja_por_unidad`: la decisión de los hechos que `ventas-7` trae desde el
2026-10-16, editado en el lugar (decisión del operador: emergencia, sin un
paquete nuevo ni Terraform).

Caso de producción del 2026-10-16 (bot V2, ventas-7): el cliente armó un
carrito con una caja de Velas de deseos cuadradas ($25.000 la caja de 12) y el
bot le dijo que el precio era por unidad («cada velita vale $25.000»). El
cliente se desanimó y un colega tuvo que corregirlo. `precio_ajeno` no lo vio:
el monto es el del catálogo; lo falso es a qué se lo pone.

La regla de palabras (`por_unidad` y `habla_de_caja`, frases del paquete) solo
PROPONE: con un producto que se cobra por caja en foco, una cláusula que dice
un precio por unidad sin nombrar la caja. Jev, con lo que dice el sistema de la
caja, decide si el texto le pone a cada vela el precio de la caja; lo que Jev
no contestó no se retiene (las frases son anchas: «cada una» habla también de
cajas). Sin Jev no revisa.

En un clon de forge `ventas-7` no viaja (experimento de esta tienda): se salta.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.capabilities.texto import CajaDelTexto
from src.plugins.chats.agent.sales.decisions.guards import decide_for_session
from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH
from src.sdk.connectorkit import TypedAnswer

V7 = BUNDLES_DIR / "ventas-7"
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()
SID = "wa_573001111111"
#: El turno 5 del caso (texto del bot).
INCIDENT = CajaDelTexto(
    text="Claro, te cuento: el precio es por unidad, cada velita vale *$25.000*.",
    productos=("Velas de deseos cuadradas",),
    por_caja=(12,),
    precios=(25_000,),
)


def _need_v7() -> None:
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    assert (V7 / "bundle.yaml").is_file(), "falta el paquete ventas-7"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _ventas_7(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    registry.reset()
    yield
    registry.reset()


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


async def _decide(inp: object, vault: Path):
    cap = registry.capability_if_present("caja_por_unidad")
    assert cap is not None
    return await decide_for_session(cap, inp, session_id=SID, vault_dir=vault)


def _with(text: str, **kw: Any) -> CajaDelTexto:
    return CajaDelTexto(**{**INCIDENT.__dict__, "text": text, **kw})


def test_ventas_7_brings_it_and_ventas_6_runs_without_it(monkeypatch) -> None:
    from src.sdk.decisionkit import check_bundle

    _need_v7()
    catalog = _load(CATALOG_PATH)

    assert check_bundle(V7, CATALOG_PATH) == []
    assert "caja_por_unidad" in _load(V7 / "bundle.yaml")["capabilities"]
    assert catalog["capabilities_since"]["caja_por_unidad"] == 7
    assert catalog["about"]["caja_por_unidad"]["where"] == ["herramientas"]
    assert registry.capability_if_present("caja_por_unidad").bundle == "ventas-7@7"
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-6")
    registry.reset()
    assert registry.capability_if_present("caja_por_unidad") is None


def test_the_rule_proposes_the_box_products_of_a_clause_that_says_a_price_per_unit() -> None:
    _need_v7()
    cap = registry.capability_if_present("caja_por_unidad")

    [item] = cap.items(INCIDENT)

    assert item["term"] == "el precio de Velas de deseos cuadradas es por unidad"
    assert item["fact"] == (
        "Velas de deseos cuadradas se cobra por caja: $25.000 es el precio de la caja de 12 unidades "
        "(la cantidad del pedido son cajas)"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Así es, *$25.000* cada una.",
        "Cada vela sale a $25.000.",
        "Las velitas sueltas valen $25.000.",
        "El precio es por unidad 🤍",
    ],
)
def test_the_rule_reads_the_ways_of_saying_per_unit(text: str) -> None:
    _need_v7()

    assert [i["term"] for i in registry.capability_if_present("caja_por_unidad").items(_with(text))] == [
        "el precio de Velas de deseos cuadradas es por unidad"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Cada caja trae 12 velitas y vale $25.000.",  # la cláusula nombra la caja
        "Te quedan a $25.000 por caja, cada caja con 12.",
        "El precio no es por unidad: es el de la caja.",  # lo niega
        "Te las dejo en Multicolor 🤍",  # no dice un precio por unidad
    ],
)
def test_the_rule_does_not_propose_the_box_or_a_denial(text: str) -> None:
    _need_v7()

    assert registry.capability_if_present("caja_por_unidad").items(_with(text)) == []


def test_without_a_product_sold_by_box_nothing_is_proposed() -> None:
    _need_v7()

    inp = _with(INCIDENT.text, productos=(), por_caja=(), precios=())

    assert registry.capability_if_present("caja_por_unidad").items(inp) == []


async def test_without_jev_it_never_holds(tmp_path: Path, oracle) -> None:
    _need_v7()

    verdict = await _decide(INCIDENT, tmp_path)

    assert (list(verdict.value), verdict.by) == ([], "reglas") and oracle["fake"].calls == []


@pytest.mark.parametrize(("p", "expected"), [(0.95, ["el precio de Velas de deseos cuadradas es por unidad"]), (0.1, [])])
async def test_jev_says_if_the_text_puts_the_box_price_on_each_candle(
    tmp_path: Path, oracle, jev, p: float, expected: list[str]
) -> None:
    _need_v7()
    qid = "caja_por_unidad.termino_1"
    oracle["fake"] = FakePerceptionAdapter({qid: TypedAnswer(id=qid, kind="noul", p=p)})

    verdict = await _decide(INCIDENT, tmp_path)

    assert (verdict.value, verdict.by) == (expected, "jev")
    [(state, questions)] = oracle["fake"].calls
    assert questions == (qid,)
    assert INCIDENT.text in state and "caja de 12 unidades" in state


async def test_jev_is_not_asked_without_a_proposal(tmp_path: Path, oracle, jev) -> None:
    _need_v7()

    verdict = await _decide(_with("Cada caja trae 12 velitas y vale $25.000."), tmp_path)

    assert list(verdict.value) == [] and oracle["fake"].calls == []
