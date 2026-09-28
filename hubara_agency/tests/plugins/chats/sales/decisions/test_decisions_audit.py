"""Auditoría del turno (diseño v2 §04, fase F6): después de responder, el
motor compara las tools que pedía el contrato (grabadas en el paso
`perception` de la traza) contra las que el turno usó. Queda en la traza
(`contract`) y es la métrica «cumplimiento del contrato» del laboratorio.
No bloquea nada.

La pregunta de respaldo sobre afirmaciones sin consultar («hay stock»,
«llega mañana») arranca en SOMBRA: su valor nunca actúa; los desacuerdos van
a la cola que califica Claude Code.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.decisions.audit import contract_compliance

REQUIRED = {"required": [{"topic": "envio", "any_of": ["send_shipping_rates"], "nudge": "Usa send_shipping_rates."},
                         {"topic": "precio", "any_of": ["search_products", "get_product_by_handle"], "nudge": "…"}]}


def _steps(*tools: str) -> list[dict]:
    return [{"i": 1, "kind": "perception", "tools": REQUIRED}, *({"kind": "tool", "name": t} for t in tools)]


def test_every_required_topic_resolved_is_a_kept_contract() -> None:
    assert contract_compliance(_steps("send_shipping_rates", "get_product_by_handle")) == {
        "required": ["envio", "precio"], "missing": [], "ok": True,
    }


def test_a_topic_without_its_tool_is_missing() -> None:
    audit = contract_compliance(_steps("send_reply"))

    assert audit["ok"] is False
    assert [m["topic"] for m in audit["missing"]] == ["envio", "precio"]


def test_without_a_contract_there_is_nothing_to_audit() -> None:
    assert contract_compliance([{"kind": "tool", "name": "send_reply"}]) is None
    assert contract_compliance([{"kind": "perception", "tools": {}}]) is None


def test_the_turn_trace_carries_the_contract_audit() -> None:
    from src.plugins.chats.agent.sales.turn_trace import enrich_turn_trace

    record = enrich_turn_trace({"steps": _steps("send_reply"), "turn_started_ms": 1}, {}, previous=None,
                               session_id="wa_1", recorded_at_ms=2)

    assert record["contract"]["ok"] is False
    assert enrich_turn_trace({"steps": [], "turn_started_ms": 1}, {}, previous=None, session_id="wa_1",
                             recorded_at_ms=2).get("contract") is None
