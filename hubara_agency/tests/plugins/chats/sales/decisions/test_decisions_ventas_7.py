"""`ventas-7`: `ventas-6` con las decisiones nuevas `aroma_ajeno`, `color_ajeno` y
`variante_ajena` y ③ del catálogo en el turno (`test_decisions_ventas_7_verifica.py`),
y nada más (esta prueba lo exige, como `test_decisions_ventas_6.py`).

Caso de producción del 2026-10-13 (bot V2, ventas-6): el cliente armó un
carrito con 2 Velas de deseos cuadradas y el bot escribió «vienen en Chanel,
Drakar y Ylang Ylang». Drakar e Ylang Ylang son de la Plegaria de Luz (salió en
la misma búsqueda); el producto tiene un solo aroma y un colega tuvo que
corregirlo en el chat. Nada revisaba que los aromas que el bot nombra sean del
producto del que habla.

`aroma_ajeno` y `color_ajeno`: la regla encuentra los aromas (o colores) del
catálogo que el texto nombra y no son del producto en foco
(`product_truth.foreign_labels`); Jev, por cada uno, solo QUITA los avisos
falsos (habla de otro producto, dice que no lo tiene). `variante_ajena`: una
presentación, signo o tamaño inventado no está en ningún catálogo, así que no
hay qué buscar: Jev lee la ficha del producto y dice si el texto le ofrece algo
que no tiene (sin Jev, la regla no marca nada). Las pide `send_reply` antes de
enviar. Las versiones publicadas no las traen (no se editan): el catálogo las
pide desde la 7 (`capabilities_since`).

En un clon de forge `ventas-7` no viaja (experimento de esta tienda): se salta.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.capabilities.texto import (
    CuponDelTexto,
    EstadoDelTexto,
    NegacionDelTexto,
    OpcionesDelTexto,
    PagoDelTexto,
    PreciosDelTexto,
    VariantesDelTexto,
)
from src.plugins.chats.agent.sales.decisions.guards import decide_for_session
from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH
from src.sdk.connectorkit import TypedAnswer

V6, V7 = BUNDLES_DIR / "ventas-6", BUNDLES_DIR / "ventas-7"
#: forge no viaja a un clon (`copy_exclude`): sin él, esto es otra tienda.
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[6] / "forge").is_dir()
SID = "wa_573001111111"
#: Las decisiones que trae ventas-7 (todas nacen en esta versión).
NEW = (
    "aroma_ajeno", "color_ajeno", "variante_ajena", "precio_ajeno",
    "pago_ajeno", "estado_ajeno", "cupon_ajeno", "negacion_falsa",
)
AROMAS = (
    "Caballero de la noche", "Limoncillo", "Lavanda", "Café", "Sándalo", "Ylan ylang",
    "Coco cremoso", "Frutos rojos", "Verde menta", "Drakar", "Chanel", "Noche de Navidad",
)
COLORS = ("gris", "Amarillo", "Café", "Lila")
INCIDENT = OpcionesDelTexto(
    text="¿Qué aroma prefieres? Las de deseos cuadradas vienen en *Chanel*, *Drakar* y *Ylang Ylang*.",
    products=("Velas de deseos cuadradas",),
    own=("Noche de Navidad",),
    labels=AROMAS,
    others=COLORS,
)
SHEET = (
    "Producto del pedido (del que hablan): Duo Zodiacal.\nAromas que tiene: Lavanda.\n"
    "Colores que tiene: Rojo, Dorado.\nPresentaciones que tiene: Aries, Leo."
)


def _need_v7() -> None:
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    assert (V7 / "bundle.yaml").is_file(), "falta el paquete ventas-7"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


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
    """El bot con Jev decidiendo todas las capacidades (sin tocar el vault)."""
    monkeypatch.setenv("DECISIONS_BOT", "B")


async def _decide(inp: object, vault: Path, name: str = "aroma_ajeno"):
    cap = registry.capability_if_present(name)
    assert cap is not None
    return await decide_for_session(cap, inp, session_id=SID, vault_dir=vault)


def test_ventas_7_is_certified() -> None:
    from src.sdk.decisionkit import check_bundle, load_bundle

    _need_v7()

    assert check_bundle(V7, CATALOG_PATH) == []
    assert load_bundle(V7, CATALOG_PATH).ref == "ventas-7@7"


def _without_catalog_verification(turn: dict[str, Any]) -> dict[str, Any]:
    """El turno sin ③ del catálogo (test_decisions_ventas_7_verifica.py): la
    pregunta y el complemento del asunto `catalogo` y lo que ve ③
    (`reply_state.contents`, `with_context`). Lo demás tiene que ser ventas-6."""
    q = turn["questionnaire"]
    topics = [{k: v for k, v in t.items() if not (t["id"] == "catalogo" and k in {"verify", "complement"})}
              for t in q["topics"]]
    reply_state = {k: v for k, v in q["reply_state"].items() if k not in {"contents", "with_context"}}
    return {**turn, "questionnaire": {**q, "topics": topics, "reply_state": reply_state}}


#: El dominio de ventas-7 trae además los datos de envío/pago del 2º toque del
#: remarketing (test_remarketing_catalog_silent.py): exactamente estas llaves.
REMARKETING_FACTS = ("practical_facts", "hook_examples_practical")


def _without_remarketing_facts(domain: dict[str, Any]) -> dict[str, Any]:
    vocabulary = {k: v for k, v in domain["vocabulary"].items() if k not in REMARKETING_FACTS}
    return {**domain, "vocabulary": vocabulary}


def test_ventas_7_is_ventas_6_with_the_fact_decisions_and_nothing_else() -> None:
    _need_v7()

    new = [f"capabilities/{name}.yaml" for name in NEW]
    v6_files = sorted(str(p.relative_to(V6)) for p in V6.rglob("*.yaml"))
    v7_files = sorted(str(p.relative_to(V7)) for p in V7.rglob("*.yaml"))
    assert v7_files == sorted([*v6_files, *new])
    for rel in v6_files:
        if rel == "turn.yaml":
            assert _without_catalog_verification(_load(V7 / rel)) == _load(V6 / rel), rel
        elif rel == "domain.yaml":
            assert set(REMARKETING_FACTS) <= set(_load(V7 / rel)["vocabulary"]), rel
            assert _without_remarketing_facts(_load(V7 / rel)) == _load(V6 / rel), rel
        elif rel != "bundle.yaml":
            assert _load(V7 / rel) == _load(V6 / rel), rel
    head = _load(V7 / "bundle.yaml")
    assert {**head, "id": "ventas-6", "version": 6, "capabilities": _load(V6 / "bundle.yaml")["capabilities"]} == _load(
        V6 / "bundle.yaml"
    )
    assert head["capabilities"] == sorted([*_load(V6 / "bundle.yaml")["capabilities"], *NEW])


def test_the_catalog_asks_for_it_from_version_7_and_the_published_versions_run_without_it(monkeypatch) -> None:
    _need_v7()
    catalog = _load(CATALOG_PATH)

    assert catalog["capabilities_since"] == dict.fromkeys(NEW, 7)
    assert all(catalog["about"][n]["where"] == ["herramientas"] for n in catalog["capabilities_since"])
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-6")
    registry.reset()
    assert registry.capability_if_present("aroma_ajeno") is None
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    registry.reset()
    assert registry.capability_if_present("aroma_ajeno").bundle == "ventas-7@7"


async def test_with_rules_the_incident_is_three_foreign_aromas(tmp_path: Path, oracle) -> None:
    _need_v7()

    verdict = await _decide(INCIDENT, tmp_path)

    assert (verdict.value, verdict.by) == (["Chanel", "Drakar", "Ylan ylang"], "reglas")
    assert oracle["fake"].calls == []


async def test_jev_removes_an_aroma_the_text_gives_to_another_product(tmp_path: Path, oracle, jev) -> None:
    """«Las de deseos vienen en Noche de Navidad; la Plegaria de Luz, en
    Drakar»: la regla marca Drakar (el foco es el producto del pedido) y Jev lee
    que es de otro producto."""
    _need_v7()
    text = "Las de deseos vienen en *Noche de Navidad*. La Plegaria de Luz sí viene en *Drakar*."
    oracle["fake"] = FakePerceptionAdapter({"aroma_ajeno.termino_1": _noul("aroma_ajeno.termino_1", 0.04)})

    verdict = await _decide(OpcionesDelTexto(**{**INCIDENT.__dict__, "text": text}), tmp_path)

    assert verdict.rule == ["Drakar"]
    assert (verdict.value, verdict.by) == ([], "jev")
    [(state, questions)] = oracle["fake"].calls
    assert questions == ("aroma_ajeno.termino_1",)
    assert text in state and "Velas de deseos cuadradas" in state and "Noche de Navidad" in state


async def test_jev_keeps_what_the_text_offers_and_never_adds(tmp_path: Path, oracle, jev) -> None:
    _need_v7()
    oracle["fake"] = FakePerceptionAdapter({
        "aroma_ajeno.termino_1": _noul("aroma_ajeno.termino_1", 0.97),
        "aroma_ajeno.termino_2": _noul("aroma_ajeno.termino_2", 0.95),
        "aroma_ajeno.termino_3": _noul("aroma_ajeno.termino_3", 0.96),
    })

    verdict = await _decide(INCIDENT, tmp_path)

    assert (verdict.value, verdict.by) == (["Chanel", "Drakar", "Ylan ylang"], "jev")


async def test_without_candidates_jev_is_not_asked(tmp_path: Path, oracle, jev) -> None:
    _need_v7()
    text = "Las de deseos vienen solo en aroma *Noche de Navidad* 🎄"

    verdict = await _decide(OpcionesDelTexto(**{**INCIDENT.__dict__, "text": text}), tmp_path)

    assert verdict.value == [] and oracle["fake"].calls == []


# ── color_ajeno: la misma búsqueda con los colores ─────────────────────────

DESEOS_COLORS = OpcionesDelTexto(
    text="Te las dejo en azul y lila, ¿te sirve?",
    products=("Velas de deseos cuadradas",),
    own=("Rojo y verde", "Multicolor"),
    labels=COLORS + ("Azul", "Lila"),
    others=AROMAS,
)


async def test_with_rules_colors_of_other_products_are_foreign(tmp_path: Path, oracle) -> None:
    _need_v7()

    verdict = await _decide(DESEOS_COLORS, tmp_path, "color_ajeno")

    assert (verdict.value, verdict.by) == (["Azul", "Lila"], "reglas")
    assert oracle["fake"].calls == []


async def test_jev_removes_a_color_that_is_not_offered(tmp_path: Path, oracle, jev) -> None:
    _need_v7()
    oracle["fake"] = FakePerceptionAdapter({
        "color_ajeno.termino_1": _noul("color_ajeno.termino_1", 0.05),
        "color_ajeno.termino_2": _noul("color_ajeno.termino_2", 0.9),
    })

    verdict = await _decide(DESEOS_COLORS, tmp_path, "color_ajeno")

    assert (verdict.value, verdict.by) == (["Lila"], "jev")
    [(state, questions)] = oracle["fake"].calls
    assert "Sus colores: Rojo y verde, Multicolor." in state


# ── variante_ajena: Jev con la ficha del producto ──────────────────────────


async def test_without_jev_an_invented_presentation_is_not_seen(tmp_path: Path, oracle) -> None:
    """Sin nada que buscar, la regla no marca nada: actúa con Jev (sombra mide, canary actúa)."""
    _need_v7()

    verdict = await _decide(
        VariantesDelTexto(text="Lo tenemos en Ofiuco ✨", sheets=(SHEET,), single=()), tmp_path, "variante_ajena"
    )

    assert (verdict.value, verdict.by) == (False, "reglas") and oracle["fake"].calls == []


@pytest.mark.parametrize(("p", "expected"), [(0.95, True), (0.1, False)])
async def test_jev_reads_the_sheet_and_says_if_it_offers_what_the_product_lacks(
    tmp_path: Path, oracle, jev, p: float, expected: bool
) -> None:
    _need_v7()
    oracle["fake"] = FakePerceptionAdapter({"variante.ajena": _noul("variante.ajena", p)})

    verdict = await _decide(
        VariantesDelTexto(text="Lo tenemos en Ofiuco ✨", sheets=(SHEET,), single=()), tmp_path, "variante_ajena"
    )

    assert (verdict.value, verdict.by) == (expected, "jev")
    [(state, _questions)] = oracle["fake"].calls
    assert "Lo tenemos en Ofiuco" in state and "Presentaciones que tiene: Aries, Leo." in state


async def test_without_a_product_with_presentations_jev_is_not_asked(tmp_path: Path, oracle, jev) -> None:
    _need_v7()

    verdict = await _decide(VariantesDelTexto(text="Lo tenemos en Ofiuco ✨", sheets=(), single=()), tmp_path, "variante_ajena")

    assert verdict.value is False and oracle["fake"].calls == []



# ── precio_ajeno: los montos del texto contra el precio del catálogo ───────

#: El turno 3 del incidente: las cuadradas cuestan $25.000 en el catálogo.
PRICES = PreciosDelTexto(
    text="Sobre el precio: las velas de deseos onduladas están en *$22.500* y las cuadradas en *$23.000*.",
    products=("Velas de deseos onduladas: $22.500", "Velas de deseos cuadradas: $25.000"),
    prices=(22_500, 25_000),
    policy=(45_000, 7_900, 16_940),
)


async def test_with_rules_a_price_the_catalog_does_not_have_is_foreign(tmp_path: Path, oracle) -> None:
    _need_v7()

    verdict = await _decide(PRICES, tmp_path, "precio_ajeno")

    assert (verdict.value, verdict.by) == (["$23.000"], "reglas")


@pytest.mark.parametrize(
    "text",
    [
        "Las dos cuadradas te quedan en $50.000.",              # 2 × $25.000
        "El total con envío nacional es de $66.940.",            # $50.000 + $16.940
        "El pago contra entrega aplica desde $45.000 en productos.",  # el mínimo de la política
        "Si agregas otra te faltan $20.000 para pagar contra entrega.",  # $45.000 − $25.000
    ],
)
async def test_quantities_totals_with_shipping_and_policy_amounts_are_not_foreign(
    tmp_path: Path, oracle, text: str
) -> None:
    _need_v7()

    verdict = await _decide(PreciosDelTexto(**{**PRICES.__dict__, "text": text}), tmp_path, "precio_ajeno")

    assert verdict.value == []


async def test_jev_removes_an_amount_that_is_not_the_price_of_the_product(tmp_path: Path, oracle, jev) -> None:
    """«El abono para separarlas es de $10.000»: no es un precio (Jev lo quita).
    «Te faltan $20.000 para el contra entrega» ni llega a Jev: lo explica la
    política ($45.000 − $25.000)."""
    _need_v7()
    text = "El abono para separarlas es de $10.000 y el resto lo pagas al recibir."
    oracle["fake"] = FakePerceptionAdapter({"precio_ajeno.termino_1": _noul("precio_ajeno.termino_1", 0.05)})

    verdict = await _decide(PreciosDelTexto(**{**PRICES.__dict__, "text": text}), tmp_path, "precio_ajeno")

    assert verdict.rule == ["$10.000"]
    assert (verdict.value, verdict.by) == ([], "jev")
    [(state, _questions)] = oracle["fake"].calls
    assert "Velas de deseos cuadradas: $25.000" in state



# ── pago, estado, cupón y negación: la regla de palabras propone, solo Jev decide ──
#
# Revisión del 2026-10-13: con la regla sola, frases ciertas («te he enviado el
# link», «una vez despachado…», «las velas de Halloween están en promoción»)
# retenían el mensaje. Estas cuatro no actúan sin Jev (`rule: no_items`): en
# `reglas` y en sombra no se retiene nada; con Jev, lo que la regla propone
# queda solo si Jev lee que el texto lo afirma.

PAYMENT = PagoDelTexto(
    text="Claro, puedes pagar contra entrega cuando te lleguen 🤍",
    subtotal=25_000,
    cod_min=45_000,
    surcharges=("1,5 %", "2,69 %"),
)
STATUS = EstadoDelTexto(text="¡Listo! Tu pago quedó confirmado 🤍", order="#67", stage="preparing", pay_status="pending")
COUPON = CuponDelTexto(
    text="Te aplico el cupón AMOR2026 y te queda con descuento 🤍",
    known=("AMOR2026", "BIENVENIDA"),
    valid=("BIENVENIDA",),
    valid_lines=("BIENVENIDA: 5 % en la primera compra",),
    valid_percents=(5,),
)
DENIAL = NegacionDelTexto(
    text="Lo siento, no tenemos la Plegaria de Luz por ahora.",
    titles=("Plegaria de Luz", "Velas de deseos cuadradas"),
    own=("Noche de Navidad", "Multicolor", "Rojo y verde"),
    products=("Velas de deseos cuadradas",),
)
def _yes(oracle, name: str) -> None:
    """Jev de prueba que lee que el texto sí lo afirma (0,95) en cada candidato."""
    oracle["fake"] = FakePerceptionAdapter(
        {f"{name}.termino_{n}": _noul(f"{name}.termino_{n}", 0.95) for n in range(1, 6)}
    )


KEYWORD_CASES = [
    ("pago_ajeno", PAYMENT, ["puede pagar su pedido contra entrega"]),
    ("estado_ajeno", STATUS, ["el pago de su pedido quedó confirmado"]),
    ("cupon_ajeno", COUPON, ["AMOR2026"]),
    ("negacion_falsa", DENIAL, ["Plegaria de Luz"]),
]


@pytest.mark.parametrize(("name", "inp", "_proposed"), KEYWORD_CASES)
async def test_with_rules_alone_a_keyword_decision_never_holds(tmp_path: Path, oracle, name, inp, _proposed) -> None:
    _need_v7()

    verdict = await _decide(inp, tmp_path, name)

    assert (tuple(verdict.value), verdict.by) == ((), "reglas") and oracle["fake"].calls == []


@pytest.mark.parametrize(("name", "inp", "proposed"), KEYWORD_CASES)
async def test_with_jev_what_the_rule_proposes_and_jev_confirms_holds(tmp_path: Path, oracle, jev, name, inp, proposed) -> None:
    """Jev lee que sí en cada candidato: queda exactamente lo que la regla propuso."""
    _need_v7()
    _yes(oracle, name)

    verdict = await _decide(inp, tmp_path, name)

    assert (verdict.value, verdict.by) == (proposed, "jev")
    [(state, _questions)] = oracle["fake"].calls
    assert "Lo que dice el sistema" in state


@pytest.mark.parametrize(
    ("text", "proposed"),
    [
        ("Claro, puedes pagar contra entrega cuando te lleguen 🤍", ["puede pagar su pedido contra entrega"]),
        ("Con el link de pago hay un recargo del 3 %.", ["el recargo del link de pago es del 3 %"]),
        ("Contra entrega es desde $45.000 en productos; para tu pedido te paso el link.", []),
        ("Si agregas otra vela ya puedes pagar contra entrega.", []),
    ],
)
async def test_payment_proposals(tmp_path: Path, oracle, jev, text: str, proposed: list[str]) -> None:
    _need_v7()
    _yes(oracle, "pago_ajeno")

    verdict = await _decide(PagoDelTexto(**{**PAYMENT.__dict__, "text": text}), tmp_path, "pago_ajeno")

    assert list(verdict.value) == proposed


async def test_jev_removes_cash_on_delivery_it_does_not_offer(tmp_path: Path, oracle, jev) -> None:
    _need_v7()
    text = "¿Contra entrega? Esa opción queda para pedidos más grandes 🤍"
    oracle["fake"] = FakePerceptionAdapter({"pago_ajeno.termino_1": _noul("pago_ajeno.termino_1", 0.05)})

    verdict = await _decide(PagoDelTexto(**{**PAYMENT.__dict__, "text": text}), tmp_path, "pago_ajeno")

    assert (list(verdict.value), verdict.by) == ([], "jev")
    [(state, _questions)] = oracle["fake"].calls
    assert "suma $25.000 en productos" in state and "desde $45.000" in state


@pytest.mark.parametrize(
    ("text", "pay_status", "stage", "proposed"),
    [
        ("Tu pedido ya va en camino 🚚", "pending", "preparing", ["su pedido ya salió o va en camino"]),
        ("Tu pedido ya fue entregado.", "pending", "shipping", ["su pedido ya fue entregado"]),
        ("Tu pago quedó confirmado y tu pedido ya va en camino 🚚", "paid", "shipping", []),
        ("Te avisamos apenas esté en camino 🤍", "pending", "preparing", []),
    ],
)
async def test_status_proposals(tmp_path: Path, oracle, jev, text, pay_status, stage, proposed) -> None:
    _need_v7()
    _yes(oracle, "estado_ajeno")
    inp = EstadoDelTexto(text=text, order="#67", stage=stage, pay_status=pay_status)

    assert list((await _decide(inp, tmp_path, "estado_ajeno")).value) == proposed


@pytest.mark.parametrize(
    ("text", "proposed"),
    [
        ("Hoy te dejo un descuento del 15 % 🤍", ["un descuento del 15 %"]),
        ("Con el cupón BIENVENIDA tienes 5 % de descuento en tu primera compra.", []),
        ("¿Te paso el link de pago?", []),
    ],
)
async def test_coupon_proposals(tmp_path: Path, oracle, jev, text: str, proposed: list[str]) -> None:
    _need_v7()
    _yes(oracle, "cupon_ajeno")

    assert list((await _decide(CuponDelTexto(**{**COUPON.__dict__, "text": text}), tmp_path, "cupon_ajeno")).value) == proposed


@pytest.mark.parametrize(
    ("text", "proposed"),
    [
        ("Las de deseos ya no vienen en Multicolor, solo Rojo y verde.", ["Multicolor"]),
        ("¿No te gustaría la Plegaria de Luz también?", []),
        ("Las de deseos no vienen en Chanel: su aroma es Noche de Navidad.", []),
        # «no viene en» habla de un aroma o un color, no de tener el producto (sonda 2026-10-13: 0,39).
        ("La Plegaria de Luz no viene en Chanel, pero sí en Lavanda.", []),
    ],
)
async def test_denial_proposals(tmp_path: Path, oracle, jev, text: str, proposed: list[str]) -> None:
    _need_v7()
    _yes(oracle, "negacion_falsa")

    assert list((await _decide(NegacionDelTexto(**{**DENIAL.__dict__, "text": text}), tmp_path, "negacion_falsa")).value) == proposed


# ── variante_ajena con un producto de presentación única ───────────────────

SINGLE = (
    "Producto del pedido (del que hablan): Cubo Love.\nAromas que tiene: Lavanda.\nColores que tiene: Rosado.\n"
    "Presentaciones que tiene: una sola (no viene en otras formas, envases, tamaños ni versiones)."
)


async def test_jev_reads_a_single_presentation_when_the_text_names_one(tmp_path: Path, oracle, jev) -> None:
    """Remarketing 2026-09-25: «el Cubo Love también viene en vaso» (no existe)."""
    _need_v7()
    oracle["fake"] = FakePerceptionAdapter({"variante.ajena": _noul("variante.ajena", 0.93)})
    inp = VariantesDelTexto(text="El Cubo Love también viene en vaso 🤍", sheets=(), single=(SINGLE,))

    verdict = await _decide(inp, tmp_path, "variante_ajena")

    assert (verdict.value, verdict.by) == (True, "jev")
    [(state, _questions)] = oracle["fake"].calls
    assert "una sola" in state


async def test_a_single_presentation_is_not_asked_without_a_presentation_word(tmp_path: Path, oracle, jev) -> None:
    _need_v7()
    inp = VariantesDelTexto(text="¿Te gustó el Cubo Love? Viene en Lavanda 🤍", sheets=(), single=(SINGLE,))

    verdict = await _decide(inp, tmp_path, "variante_ajena")

    assert verdict.value is False and oracle["fake"].calls == []
