"""`ventas-7`: `cajas` — ¿la cantidad que va a anotar el asesor son cajas o
velas? (laboratorio de ···0686, 2026-10-14).

Las velas de deseos se cobran por caja: la cuadrada trae 12 velas a $25.000 y
la ondulada 10 a $22.500. «Dos cajas de 12» se anotó `cantidad: 24` dos de
cuatro veces y el resumen salió con 24 cajas ($600.000), aunque el bot había
escrito «dos cajas son 24 velas: $50.000».

El código arma la comparación (`product_options.pack_units`: cuántas trae la
caja; las dos cuentas con su precio) y Jev lee lo que escribió el cliente y
contesta una sola pregunta: ¿pide esa cantidad de CAJAS? Solo se pregunta
desde una caja entera (24 de 12, no 2). Sin Jev, la regla no sabe: «duda» y
el texto vuelve al modelo con las dos cuentas.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas_pedido import CajasDelPedido
from src.plugins.chats.agent.sales.decisions.guards import decide_for_session
from src.sdk.connectorkit import TypedAnswer
from tests.plugins.chats.sales.decisions.test_decisions_ventas_7 import IN_FORGE_CLONE

SID = "wa_573001111111"
INCIDENT = CajasDelPedido(
    producto="Velas de deseos cuadradas", cantidad=24, por_caja=12, precio=25_000,
    charla="[asesor] ¿Te la dejo en rojo y verde o en multicolor?\n[cliente] Dos cajas de 12 como seria el envio y el pago",
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


def _jev(monkeypatch, oracle, p: float | None) -> None:
    monkeypatch.setenv("DECISIONS_BOT", "B")
    answers = {} if p is None else {"cajas.pide_cajas": TypedAnswer(id="cajas.pide_cajas", kind="noul", p=p)}
    oracle["fake"] = FakePerceptionAdapter(answers)


async def _decide(inp: CajasDelPedido, vault: Path):
    cap = registry.capability_if_present("cajas")
    assert cap is not None
    return await decide_for_session(cap, inp, session_id=SID, vault_dir=vault)


async def test_with_rules_a_whole_box_or_more_is_doubt(tmp_path: Path, oracle) -> None:
    verdict = await _decide(INCIDENT, tmp_path)

    assert (verdict.value, verdict.by) == ("duda", "reglas") and oracle["fake"].calls == []


async def test_less_than_a_box_is_boxes_and_jev_is_not_asked(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.05)

    verdict = await _decide(CajasDelPedido(**{**INCIDENT.__dict__, "cantidad": 2}), tmp_path)

    assert verdict.value == "cajas" and oracle["fake"].calls == []


async def test_jev_reads_that_the_quantity_is_candles(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.04)

    verdict = await _decide(INCIDENT, tmp_path)

    assert (verdict.value, verdict.by) == ("velas", "jev")
    [(state, questions)] = oracle["fake"].calls
    assert questions == ("cajas.pide_cajas",)
    assert INCIDENT.charla in state
    assert "24 cajas = 288 velas, $600.000" in state and "24 velas = 2 cajas, $50.000" in state


async def test_jev_reads_that_the_quantity_is_boxes(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.95)
    inp = CajasDelPedido(**{**INCIDENT.__dict__, "charla": "[cliente] Necesito 24 cajas para regalar en la empresa"})

    assert (await _decide(inp, tmp_path)).value == "cajas"


async def test_when_jev_doubts_the_rule_sends_it_back(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.5)

    assert (await _decide(INCIDENT, tmp_path)).value == "duda"


async def test_without_what_the_customer_wrote_jev_is_not_asked(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.04)

    verdict = await _decide(CajasDelPedido(**{**INCIDENT.__dict__, "charla": "[asesor] ¿Cuántas quieres?"}), tmp_path)

    assert verdict.value == "duda" and oracle["fake"].calls == []
