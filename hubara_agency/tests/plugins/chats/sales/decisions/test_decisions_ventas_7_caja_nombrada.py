"""`ventas-7`: `caja_nombrada` — el cliente nombró la caja de OTRO producto
(laboratorio de ···0686, 2026-10-14).

«Dos cajas de 12, ¿cómo sería el envío y el pago?» y el bot anotó las velas
de deseos onduladas, que traen 10 (la de 12 es la cuadrada); una vez hasta le
escribió «cada caja trae 10 velas, no 12». El código arma la comparación (el
cliente nombró el tamaño de la otra caja y no el de esta) y Jev contesta una
sola pregunta: ¿quiere el otro producto, el de la caja que nombró? Sin Jev la
regla no sabe: «duda» y el bot lo revisa una vez.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas_pedido import CajaNombrada
from src.plugins.chats.agent.sales.decisions.guards import decide_for_session
from src.sdk.connectorkit import TypedAnswer
from tests.plugins.chats.sales.decisions.test_decisions_ventas_7 import IN_FORGE_CLONE

SID = "wa_573001111111"
QUESTION = "caja_nombrada.pide_la_otra"
INCIDENT = CajaNombrada(
    producto="Velas de deseos onduladas", por_caja=10, otro="Velas de deseos cuadradas", otro_por_caja=12,
    charla="[cliente] Dos cajas de 12 como seria el envio y el pago\n[cliente] Multicolor para todas, pago contra entrega",
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
    answers = {} if p is None else {QUESTION: TypedAnswer(id=QUESTION, kind="noul", p=p)}
    oracle["fake"] = FakePerceptionAdapter(answers)


async def _decide(inp: CajaNombrada, vault: Path):
    cap = registry.capability_if_present("caja_nombrada")
    assert cap is not None
    return await decide_for_session(cap, inp, session_id=SID, vault_dir=vault)


async def test_with_rules_it_is_doubt(tmp_path: Path, oracle) -> None:
    verdict = await _decide(INCIDENT, tmp_path)

    assert (verdict.value, verdict.by) == ("duda", "reglas") and oracle["fake"].calls == []


async def test_jev_reads_that_the_customer_asked_for_the_other_box(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.9)

    verdict = await _decide(INCIDENT, tmp_path)

    assert (verdict.value, verdict.by) == ("otro", "jev")
    [(state, questions)] = oracle["fake"].calls
    assert questions == (QUESTION,)
    assert INCIDENT.charla in state
    assert "El asesor va a anotar Velas de deseos onduladas: viene en caja de 10 velas." in state
    assert "La caja de 12 velas es otro producto: Velas de deseos cuadradas." in state


async def test_jev_reads_that_it_is_this_product(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.05)
    inp = CajaNombrada(**{**INCIDENT.__dict__, "charla": "[cliente] quiero 12 velitas de las onduladas"})

    assert (await _decide(inp, tmp_path)).value == "este"


async def test_when_jev_doubts_the_rule_sends_it_back(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.5)

    assert (await _decide(INCIDENT, tmp_path)).value == "duda"


async def test_without_what_the_customer_wrote_jev_is_not_asked(tmp_path: Path, oracle, monkeypatch) -> None:
    _jev(monkeypatch, oracle, 0.9)

    verdict = await _decide(CajaNombrada(**{**INCIDENT.__dict__, "charla": "[asesor] ¿Cuántas cajas?"}), tmp_path)

    assert verdict.value == "duda" and oracle["fake"].calls == []
