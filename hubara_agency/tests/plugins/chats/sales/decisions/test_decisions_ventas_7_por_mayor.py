"""`ventas-7`: `por_mayor` — el bot va a pasar el caso a un colega como pedido
al por mayor, ¿o el cliente cuenta velas sueltas? (laboratorio, 2026-10-14).

Las velas de deseos se cobran por caja (12 la cuadrada, 10 la ondulada). «Me
vendes 36 velitas?» y «quiero 30 velitas de las onduladas» se pasaron a un
colega con `BULK_ORDER` («>20 unidades»): eran 3 cajas. El código arma la
comparación (las cifras del cliente que pasan de 20 pero, contadas en velas,
caben en 20 cajas o menos, y las cuentas en cada caja) y Jev contesta dos
preguntas: ¿pide uno de los productos que van por caja? y ¿su número cuenta
velas sueltas? (una sola, compuesta, quedaba en duda con «36 velitas»: sonda
del 2026-10-14). Sin Jev la regla no sabe: «duda» y el bot lo revisa una vez.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas_pedido import PedidoPorMayor
from src.plugins.chats.agent.sales.decisions.guards import decide_for_session
from src.sdk.connectorkit import TypedAnswer
from tests.plugins.chats.sales.decisions.test_decisions_ventas_7 import IN_FORGE_CLONE

SID = "wa_573001111111"
PRODUCT = "por_mayor.producto_por_caja"
QUESTION = "por_mayor.velas_sueltas"
INCIDENT = PedidoPorMayor(
    charla="[cliente] Hola! me vendes 36 velitas?\n[asesor, nota para el colega] Cliente pide 36 velitas",
    cifras=(36,),
    productos=("Velas de deseos cuadradas", "Velas de deseos onduladas"),
    por_caja=(12, 10),
)


@pytest.fixture(autouse=True)
def _ventas_7(monkeypatch):
    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
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


def _jev(monkeypatch, oracle, loose: float, product: float = 0.9) -> None:
    monkeypatch.setenv("DECISIONS_BOT", "B")
    oracle["fake"] = FakePerceptionAdapter({
        PRODUCT: TypedAnswer(id=PRODUCT, kind="noul", p=product),
        QUESTION: TypedAnswer(id=QUESTION, kind="noul", p=loose),
    })


async def _decide(inp: PedidoPorMayor, vault: Path):
    cap = registry.capability_if_present("por_mayor")
    assert cap is not None
    return await decide_for_session(cap, inp, session_id=SID, vault_dir=vault)


async def test_with_rules_a_count_that_fits_in_boxes_is_doubt(tmp_path: Path, oracle) -> None:
    verdict = await _decide(INCIDENT, tmp_path)

    assert (verdict.value, verdict.by) == ("duda", "reglas") and oracle["fake"].calls == []


async def test_without_such_a_count_it_is_bulk_and_jev_is_not_asked(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.95)

    verdict = await _decide(PedidoPorMayor(**{**INCIDENT.__dict__, "cifras": ()}), tmp_path)

    assert verdict.value == "por_mayor" and oracle["fake"].calls == []


async def test_jev_reads_loose_candles(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.93)

    verdict = await _decide(INCIDENT, tmp_path)

    assert (verdict.value, verdict.by) == ("velas", "jev")
    [(state, questions)] = oracle["fake"].calls
    assert set(questions) == {PRODUCT, QUESTION}
    assert INCIDENT.charla in state
    assert "Velas de deseos cuadradas: caja de 12 velas" in state and "más de 20 cajas" in state
    assert "36 velas = 3 cajas" in state and "36 velas no son cajas enteras (cada caja trae 10)" in state


async def test_jev_reads_a_real_bulk_order(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.04)
    inp = PedidoPorMayor(**{**INCIDENT.__dict__, "charla": "[cliente] Necesito 25 cajas de velas de deseos cuadradas",
                            "cifras": (25,)})

    assert (await _decide(inp, tmp_path)).value == "por_mayor"


async def test_another_product_is_a_bulk_order(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.9, product=0.05)
    inp = PedidoPorMayor(**{**INCIDENT.__dict__, "charla": "[cliente] Hola, necesito 30 velas Luz Serena para un evento",
                            "cifras": (30,)})

    assert (await _decide(inp, tmp_path)).value == "por_mayor"


async def test_when_jev_doubts_the_rule_sends_it_back(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.5)

    assert (await _decide(INCIDENT, tmp_path)).value == "duda"


async def test_without_what_the_customer_wrote_jev_is_not_asked(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.93)

    verdict = await _decide(PedidoPorMayor(**{**INCIDENT.__dict__, "charla": "[asesor] ¿Cuántas quieres?"}), tmp_path)

    assert verdict.value == "duda" and oracle["fake"].calls == []
