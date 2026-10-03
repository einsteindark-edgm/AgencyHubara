"""Lecturas del cliente sobre el pedido con el motor (diseño v2 §07, familia A,
fase F3): cupón, fuera de catálogo y cantidad.

Cada una es una capacidad con la regla de hoy de respaldo:

* cupón — noul «¿Habla del cupón o de sus productos?» (ingest). Regla:
  `coupon_in_play`. Decide si se relee el cupo y qué tan larga es la nota.
* fuera de catálogo — el regex propone candidatos y, por cada término, noul
  «¿Es algo que el cliente pide o muestra?» (ingest). Jev solo puede QUITAR
  candidatos falsos, nunca inventar términos; la nota la arma el código de hoy
  con los que quedan.
* cantidad — noul «¿El asesor preguntó cuántas?» y choice «¿Qué cantidad dio?»
  {1…20, otra, ninguna} (activity del prompt). Regla: `agent_asked_quantity` +
  `parse_leading_quantity`. El valor sale de la lista cerrada y lo escribe el
  código con sus compuertas.

Para cada una: (a) con `reglas` es la regla de hoy y Jev no se consulta,
(b) con `jev` se corrige un falso positivo/negativo concreto, (c) si Jev falla
o duda decide la regla, (d) en `sombra` decide la regla y el desacuerdo va a
la cola que califica Claude Code.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas_pedido import (
    Cantidad,
    Cupon,
    CuponEnJuego,
    FueraDeCatalogo,
    PedidoDelCliente,
    RespuestaDeCantidad,
)
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog
from src.plugins.chats.agent.sales.decisions.guards import decide_for_session
from src.plugins.chats.agent.sales.use_cases.coupons import coupon_in_play
from src.plugins.chats.agent.sales.use_cases.quantity_capture import read_reply_quantity
from src.plugins.chats.shared.product_truth import unavailable_terms
from src.sdk.catalogkit import CatalogProductDTO, CatalogVariantDTO
from src.sdk.connectorkit import PromotionDTO, TypedAnswer

SID = "wa_573001234567"


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


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
    """El bot con Jev decidiendo todas las capacidades (sin tocar el vault)."""
    monkeypatch.setenv("DECISIONS_BOT", "B")


@pytest.fixture
def shadow(monkeypatch, tmp_path: Path):
    """Todas las capacidades de este archivo en sombra, por el control de producción."""
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    bots.write_capability_modes(tmp_path, {"cupon": "shadow", "fuera_de_catalogo": "shadow", "cantidad": "shadow"})


async def _decide(capability, inp, vault: Path):
    return await decide_for_session(capability, inp, session_id=SID, vault_dir=vault)


# --- cupón -------------------------------------------------------------------


def _promo(**kw) -> PromotionDTO:
    base = dict(
        id="promo_amor", code="AMOR2026", discount_type="percentage", value=10, currency_code=None,
        target_type="items", allocation="across", max_quantity=None, product_ids=("prod_cubo",), variant_ids=(),
        collection_ids=(), min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=None, budget_type=None, budget_limit=None, budget_used=None, description="Amor y amistad",
    )
    return PromotionDTO(**{**base, **kw})


def _coupon_md(*, item: dict | None = None, **promo) -> dict:
    episode: dict = {
        "episode_id": "ep_1", "closed_at_ms": None,
        "applied_coupon": {
            "code": "AMOR2026", "promotion": asdict(_promo(**promo)), "applied_at_ms": 1,
            "eligible_products": [{"handle": "cubo-love", "title": "Cubo Love"}],
        },
    }
    if item is not None:
        episode["order_draft"] = {"slots": dict(item), "items": [dict(item)]}
    return {"episodes": [episode]}


@pytest.mark.parametrize(
    ("metadata", "text"),
    [
        (_coupon_md(), "¿Y el cupón que me llegó?"),
        (_coupon_md(), "quiero el Cubo Love"),
        (_coupon_md(), "Te paso el código postal: 110111"),
        (_coupon_md(), "¿Todavía aplica el beneficio del mensaje?"),
        (_coupon_md(), "¿hacen envíos a Cali?"),
        (_coupon_md(item={"producto": "Cubo Love"}), "¿y cuánto se demora?"),
        (_coupon_md(product_ids=()), "gracias"),
        ({"episodes": [{"episode_id": "ep_1", "closed_at_ms": None}]}, "¿tienen algún cupón?"),
    ],
)
async def test_coupon_with_rules_is_todays_coupon_in_play(tmp_path: Path, oracle, metadata: dict, text: str) -> None:
    verdict = await _decide(Cupon(), CuponEnJuego(metadata=metadata, text=text), tmp_path)

    assert (verdict.value, verdict.by) == (coupon_in_play(metadata, text), "reglas")
    assert oracle["fake"].calls == []


async def test_jev_sees_a_postal_code_is_not_the_coupon(tmp_path: Path, oracle, jev) -> None:
    """Falso positivo de la regla: «código» saca el cupón a la charla aunque el
    cliente hable del código postal (se relee el cupo y la nota lo empuja)."""
    oracle["fake"] = FakePerceptionAdapter({"cupon.habla": _noul("cupon.habla", 0.03)})

    verdict = await _decide(Cupon(), CuponEnJuego(metadata=_coupon_md(), text="Te paso el código postal: 110111"), tmp_path)

    assert (verdict.rule, verdict.value, verdict.by) == (True, False, "jev")


async def test_jev_sees_the_customer_asking_for_the_benefit(tmp_path: Path, oracle, jev) -> None:
    """Falso negativo: «¿Todavía aplica el beneficio?» no trae ninguna palabra
    del regex y el cupo no se relee (prueba del 2026-09-24: «Sí, claro» con
    cupo 1)."""
    oracle["fake"] = FakePerceptionAdapter({"cupon.habla": _noul("cupon.habla", 0.95)})
    events = [{"role": "assistant", "content": "Con AMOR2026 el Cubo Love te queda con 10 % de descuento 💝"}]

    verdict = await _decide(
        Cupon(), CuponEnJuego(metadata=_coupon_md(), text="¿Todavía aplica el beneficio del mensaje?", events=events),
        tmp_path,
    )

    assert (verdict.rule, verdict.value, verdict.by) == (False, True, "jev")
    [(state, questions)] = oracle["fake"].calls
    assert questions == ("cupon.habla",)
    assert "AMOR2026" in state and "Cubo Love" in state and "10 % de descuento" in state


@pytest.mark.parametrize(
    "metadata", [_coupon_md(product_ids=()), _coupon_md(target_type="shipping_methods"), {}],
    ids=["todo-el-catalogo", "de-envio", "sin-cupon"],
)
async def test_jev_is_not_asked_when_todays_answer_does_not_read_the_text(tmp_path: Path, oracle, jev, metadata) -> None:
    verdict = await _decide(Cupon(), CuponEnJuego(metadata=metadata, text="Te paso el código postal"), tmp_path)

    assert verdict.value is coupon_in_play(metadata, "Te paso el código postal")
    assert oracle["fake"].calls == []


@pytest.mark.parametrize(
    ("fake", "reason"),
    [(FakePerceptionAdapter({}, error="http_503"), "http_503"),
     (FakePerceptionAdapter({"cupon.habla": _noul("cupon.habla", 0.5)}), "duda")],
)
async def test_coupon_falls_back_to_the_rule_when_jev_fails_or_doubts(tmp_path: Path, oracle, jev, fake, reason) -> None:
    oracle["fake"] = fake

    verdict = await _decide(Cupon(), CuponEnJuego(metadata=_coupon_md(), text="Te paso el código postal: 110111"), tmp_path)

    assert (verdict.value, verdict.by, verdict.reason) == (True, "respaldo", reason)


async def test_coupon_in_shadow_keeps_the_rule_and_queues_the_disagreement(tmp_path: Path, oracle, shadow) -> None:
    oracle["fake"] = FakePerceptionAdapter({"cupon.habla": _noul("cupon.habla", 0.02)})

    verdict = await _decide(Cupon(), CuponEnJuego(metadata=_coupon_md(), text="Te paso el código postal: 110111"), tmp_path)

    assert (verdict.value, verdict.by, verdict.jev) == (True, "reglas", False)
    [item] = DisagreementLog(tmp_path).pending()
    assert (item["capability"], item["rule"], item["jev"]) == ("cupon", True, False)


# --- fuera de catálogo -------------------------------------------------------

_TAGS = ["Aroma: Café", "Aroma: Lavanda", "Color: Azul", "Color: Rosado", "Color: Rojo"]


def _product(title: str, handle: str, description: str = "") -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published", description=description,
        variants=[CatalogVariantDTO(id=f"var_{handle}", title="Unico")], tags=list(_TAGS), options={"Unico": ["Unico"]},
    )


CATALOG = (
    _product("Cubo Love", "cubo-love", "La clásica vela cúbica con la palabra LOVE."),
    _product("Cilindro Love", "cilindro-love", "Vela cilíndrica con la palabra LOVE."),
)
CARTAGENA = "Vivo en Cartagena, ¿la tienen en vaso?"


@pytest.mark.parametrize(
    "text",
    [CARTAGENA, "Estás y en vaso también", "[el cliente envió una foto: vela rosa con diseño de dragón]",
     "¿El cubo viene en azul?", ""],
)
async def test_gap_with_rules_is_todays_unavailable_terms(tmp_path: Path, oracle, text: str) -> None:
    verdict = await _decide(FueraDeCatalogo(), PedidoDelCliente(text=text, products=CATALOG), tmp_path)

    assert (verdict.value, verdict.by) == (unavailable_terms(text, list(CATALOG)), "reglas")
    assert oracle["fake"].calls == []


async def test_jev_removes_a_city_the_regex_took_for_a_product(tmp_path: Path, oracle, jev) -> None:
    """Falso positivo: «en Cartagena» parece un pedido («en vaso») y la nota
    le decía al bot que Cartagena no existe en el catálogo."""
    oracle["fake"] = FakePerceptionAdapter({
        "fuera_de_catalogo.termino_1": _noul("fuera_de_catalogo.termino_1", 0.03),
        "fuera_de_catalogo.termino_2": _noul("fuera_de_catalogo.termino_2", 0.96),
    })

    verdict = await _decide(FueraDeCatalogo(), PedidoDelCliente(text=CARTAGENA, products=CATALOG), tmp_path)

    assert verdict.rule == ["cartagena", "vaso"]
    assert (verdict.value, verdict.by) == (["vaso"], "jev")
    [(state, questions)] = oracle["fake"].calls
    assert questions == ("fuera_de_catalogo.termino_1", "fuera_de_catalogo.termino_2") and CARTAGENA in state


async def test_jev_can_only_remove_terms_never_invent_them(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({})  # sin respuestas fijas: dice que sí a todo

    verdict = await _decide(FueraDeCatalogo(), PedidoDelCliente(text=CARTAGENA, products=CATALOG), tmp_path)

    assert set(verdict.value) <= set(verdict.rule)
    assert [q for _, qs in oracle["fake"].calls for q in qs] == [
        "fuera_de_catalogo.termino_1", "fuera_de_catalogo.termino_2"
    ]


async def test_without_candidates_jev_is_not_asked(tmp_path: Path, oracle, jev) -> None:
    verdict = await _decide(FueraDeCatalogo(), PedidoDelCliente(text="¿El cubo viene en azul?", products=CATALOG), tmp_path)

    assert verdict.value == [] and oracle["fake"].calls == []


async def test_gap_falls_back_to_the_rule_when_jev_fails(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({}, error="timeout")

    verdict = await _decide(FueraDeCatalogo(), PedidoDelCliente(text=CARTAGENA, products=CATALOG), tmp_path)

    assert (verdict.value, verdict.by) == (["cartagena", "vaso"], "respaldo")


async def test_a_doubtful_term_stays(tmp_path: Path, oracle, jev) -> None:
    """Quitar un término exige certeza (p ≤ 0,15): en la duda queda."""
    oracle["fake"] = FakePerceptionAdapter({
        "fuera_de_catalogo.termino_1": _noul("fuera_de_catalogo.termino_1", 0.4),
        "fuera_de_catalogo.termino_2": _noul("fuera_de_catalogo.termino_2", 0.9),
    })

    verdict = await _decide(FueraDeCatalogo(), PedidoDelCliente(text=CARTAGENA, products=CATALOG), tmp_path)

    assert verdict.value == ["cartagena", "vaso"]


async def test_gap_in_shadow_keeps_the_rule_and_queues_the_disagreement(tmp_path: Path, oracle, shadow) -> None:
    oracle["fake"] = FakePerceptionAdapter({
        "fuera_de_catalogo.termino_1": _noul("fuera_de_catalogo.termino_1", 0.02),
        "fuera_de_catalogo.termino_2": _noul("fuera_de_catalogo.termino_2", 0.97),
    })

    verdict = await _decide(FueraDeCatalogo(), PedidoDelCliente(text=CARTAGENA, products=CATALOG), tmp_path)

    assert (verdict.value, verdict.by) == (["cartagena", "vaso"], "reglas")
    [item] = DisagreementLog(tmp_path).pending()
    assert (item["capability"], item["rule"], item["jev"]) == ("fuera_de_catalogo", ["cartagena", "vaso"], ["vaso"])


# --- cantidad ----------------------------------------------------------------

ASKED = "¡Excelente elección! 🤍 El *Velón Amor Eterno* en aroma *Lavanda*.\n\n¿Cuántas unidades deseas?"


@pytest.mark.parametrize(
    ("agent", "text"),
    [
        (ASKED, "Una que colores tienes?"),
        (ASKED, "Quiero 2"),
        (ASKED, "Un regalo para mi mamá, ¿cuál me recomiendas?"),
        ("¿Te llevas una o las dos?", "Dos"),
        ("¿Qué aroma prefieres?", "una"),
        (None, "tres"),
    ],
)
async def test_quantity_with_rules_is_todays_reading(tmp_path: Path, oracle, agent, text) -> None:
    verdict = await _decide(Cantidad(), RespuestaDeCantidad(last_agent_text=agent, text=text), tmp_path)

    assert (verdict.value, verdict.by) == ({"cantidad": read_reply_quantity(agent, text)}, "reglas")
    assert oracle["fake"].calls == []


async def test_jev_reads_a_quantity_the_parser_misses(tmp_path: Path, oracle, jev) -> None:
    """Falso negativo: «Quiero 2» no ARRANCA con la cantidad y el parser
    conservador no la ve; el bot vuelve a preguntar."""
    oracle["fake"] = FakePerceptionAdapter({
        "cantidad.pregunto": _noul("cantidad.pregunto", 0.97), "cantidad.dio": _choice("cantidad.dio", "2", 0.94),
    })

    verdict = await _decide(Cantidad(), RespuestaDeCantidad(last_agent_text=ASKED, text="Quiero 2"), tmp_path)

    assert (verdict.rule, verdict.value, verdict.by) == ({"cantidad": None}, {"cantidad": 2}, "jev")


async def test_jev_reads_the_question_the_regex_misses(tmp_path: Path, oracle, jev) -> None:
    """«¿Te llevas una o las dos?» pregunta la cantidad sin decir «cuántas»."""
    oracle["fake"] = FakePerceptionAdapter({
        "cantidad.pregunto": _noul("cantidad.pregunto", 0.93), "cantidad.dio": _choice("cantidad.dio", "2", 0.9),
    })

    verdict = await _decide(Cantidad(), RespuestaDeCantidad(last_agent_text="¿Te llevas una o las dos?", text="Dos"), tmp_path)

    assert (verdict.rule, verdict.value) == ({"cantidad": None}, {"cantidad": 2})


async def test_jev_does_not_take_an_article_for_a_quantity(tmp_path: Path, oracle, jev) -> None:
    """Falso positivo: «Un regalo para mi mamá…» arranca con «un» y el parser
    fijaba Cantidad: 1 en el pedido."""
    oracle["fake"] = FakePerceptionAdapter({
        "cantidad.pregunto": _noul("cantidad.pregunto", 0.96), "cantidad.dio": _choice("cantidad.dio", "ninguna", 0.9),
    })

    verdict = await _decide(
        Cantidad(), RespuestaDeCantidad(last_agent_text=ASKED, text="Un regalo para mi mamá, ¿cuál me recomiendas?"),
        tmp_path,
    )

    assert (verdict.rule, verdict.value, verdict.by) == ({"cantidad": 1}, {"cantidad": None}, "jev")


async def test_when_the_advisor_did_not_ask_there_is_no_quantity(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({
        "cantidad.pregunto": _noul("cantidad.pregunto", 0.05), "cantidad.dio": _choice("cantidad.dio", "1", 0.99),
    })

    verdict = await _decide(Cantidad(), RespuestaDeCantidad(last_agent_text="¿Qué aroma prefieres?", text="una"), tmp_path)

    assert verdict.value == {"cantidad": None}


@pytest.mark.parametrize(
    "inp",
    [
        RespuestaDeCantidad(last_agent_text=ASKED, text="Quiero 2", open_slot=False),
        RespuestaDeCantidad(last_agent_text=None, text="Quiero 2"),
        RespuestaDeCantidad(last_agent_text=ASKED, text="[el cliente tocó el botón: Ver catálogo]"),
        RespuestaDeCantidad(last_agent_text=ASKED, text=None),
    ],
    ids=["sin-donde-escribir", "sin-asesor", "mensaje-del-sistema", "sin-texto"],
)
async def test_jev_is_not_asked_when_nothing_could_be_written_or_read(tmp_path: Path, oracle, jev, inp) -> None:
    verdict = await _decide(Cantidad(), inp, tmp_path)

    assert verdict.value == {"cantidad": read_reply_quantity(inp.last_agent_text, inp.text)}
    assert oracle["fake"].calls == []


@pytest.mark.parametrize(
    ("answers", "reason"),
    [
        ({"cantidad.pregunto": _noul("cantidad.pregunto", 0.97), "cantidad.dio": _choice("cantidad.dio", "otra", 0.9)}, "duda"),
        ({"cantidad.pregunto": _noul("cantidad.pregunto", 0.6), "cantidad.dio": _choice("cantidad.dio", "2", 0.9)}, "duda"),
        ({"cantidad.pregunto": _noul("cantidad.pregunto", 0.97), "cantidad.dio": _choice("cantidad.dio", "2", 0.5)}, "duda"),
    ],
    ids=["otra-cantidad", "no-sabe-si-pregunto", "cantidad-dudosa"],
)
async def test_quantity_falls_back_to_the_rule_when_jev_doubts(tmp_path: Path, oracle, jev, answers, reason) -> None:
    oracle["fake"] = FakePerceptionAdapter(answers)

    verdict = await _decide(Cantidad(), RespuestaDeCantidad(last_agent_text=ASKED, text="Una que colores tienes?"), tmp_path)

    assert (verdict.value, verdict.by, verdict.reason) == ({"cantidad": 1}, "respaldo", reason)


async def test_quantity_in_shadow_keeps_the_rule_and_queues_the_disagreement(tmp_path: Path, oracle, shadow) -> None:
    oracle["fake"] = FakePerceptionAdapter({
        "cantidad.pregunto": _noul("cantidad.pregunto", 0.97), "cantidad.dio": _choice("cantidad.dio", "2", 0.95),
    })

    verdict = await _decide(Cantidad(), RespuestaDeCantidad(last_agent_text=ASKED, text="Quiero 2"), tmp_path)

    assert (verdict.value, verdict.by) == ({"cantidad": None}, "reglas")
    [item] = DisagreementLog(tmp_path).pending()
    assert (item["capability"], item["rule"], item["jev"]) == ("cantidad", {"cantidad": None}, {"cantidad": 2})


def test_the_ingest_readings_wait_for_jev_like_every_capability() -> None:
    """Decisión del operador (2026-09-29): «es indispensable que siempre
    funcione con Jev». Antes el ingest cortaba a 1,5 s; ahora ninguna
    capacidad trae un tope propio: la espera es la del perfil del oráculo
    (el ingest corre después de responderle a Meta, en segundo plano)."""
    assert not any(hasattr(c, "timeout_s") for c in (Cupon, FueraDeCatalogo, Cantidad))
