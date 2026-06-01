"""Tests del helper compartido `run_agent_turn` (Fase 5).

El helper se ejecuta dentro del `@workflow.run` de los workflows productivos. Aqui
solo testeamos las piezas puras:
  * `_try_parse_decision_payload` parsea JSON con `transfer_decision`.
  * `_try_parse_decision_payload` retorna None ante texto plano o JSON invalido.
  * Las dataclasses `PendingMessage` / `TurnResult` son JSON-serializables (R-JSON).
"""
from __future__ import annotations

import json
from dataclasses import asdict

from src.platform.workflow_helpers import (
    PendingMessage,
    TurnResult,
    _try_parse_decision_payload,
)
from src.platform.contracts import (
    EscalationDecision,
    ScheduleRemarketingDecision,
    TransferDecision,
)


def test_parse_decision_with_transfer() -> None:
    raw = json.dumps({
        "transfer_decision": {
            "session_id": "wa_X",
            "target_route": "ventas",
            "summary": "interesado",
        },
        "message": "ok",
    })
    parsed = _try_parse_decision_payload(raw)
    assert parsed is not None
    assert parsed["transfer_decision"]["session_id"] == "wa_X"


def test_parse_decision_with_schedule_remarketing() -> None:
    raw = json.dumps({
        "schedule_remarketing": {
            "session_id": "wa_Y",
            "motivo": "duda precio",
            "delay_seconds": 60,
        },
        "message": "ok",
    })
    parsed = _try_parse_decision_payload(raw)
    assert parsed is not None
    assert parsed["schedule_remarketing"]["delay_seconds"] == 60


def test_parse_decision_with_escalation_decision() -> None:
    raw = json.dumps({
        "escalation_decision": {
            "session_id": "wa_E",
            "reason_category": "BULK_ORDER",
            "summary": "cliente pide 50 velas",
        },
        "message": "ok",
    })
    parsed = _try_parse_decision_payload(raw)
    assert parsed is not None
    assert parsed["escalation_decision"]["reason_category"] == "BULK_ORDER"


def test_parse_decision_returns_none_on_plain_text() -> None:
    assert _try_parse_decision_payload("not a json") is None


def test_parse_decision_returns_none_on_non_dict_json() -> None:
    assert _try_parse_decision_payload("[1, 2, 3]") is None


def test_pending_message_is_dataclass_serializable() -> None:
    pm = PendingMessage(message="hola", media=None, plugin_context=["ctx"])
    payload = asdict(pm)
    assert payload == {
        "message": "hola",
        "media": None,
        "plugin_context": ["ctx"],
        "is_handoff": False,
    }


def test_turn_result_default_serializable() -> None:
    tr = TurnResult(final_content="hi", tools_used=["foo"])
    payload = asdict(tr)
    assert payload["final_content"] == "hi"
    assert payload["tools_used"] == ["foo"]
    assert payload["transfer_decision"] is None
    assert payload["schedule_remarketing"] is None
    assert payload["escalation_decision"] is None


def test_turn_result_with_escalation_serializable() -> None:
    tr = TurnResult(
        final_content="te conecto con un colega 🤍",
        tools_used=["escalate_to_human"],
        escalation_decision=EscalationDecision(
            session_id="wa_F",
            reason_category="CORPORATE_EVENT",
            summary="50 velas para una boda en Bogota",
        ),
    )
    payload = asdict(tr)
    assert payload["escalation_decision"]["session_id"] == "wa_F"
    assert payload["escalation_decision"]["reason_category"] == "CORPORATE_EVENT"
    assert payload["transfer_decision"] is None
    assert payload["schedule_remarketing"] is None


def test_turn_result_with_decisions_serializable() -> None:
    tr = TurnResult(
        final_content="ok",
        tools_used=["transfer_to_sales_agent"],
        transfer_decision=TransferDecision(session_id="wa_A", target_route="ventas", summary="x"),
        schedule_remarketing=ScheduleRemarketingDecision(
            session_id="wa_B", motivo="m", delay_seconds=60
        ),
    )
    payload = asdict(tr)
    assert payload["transfer_decision"]["session_id"] == "wa_A"
    assert payload["schedule_remarketing"]["motivo"] == "m"


def test_workflow_helpers_binds_all_decision_symbols() -> None:
    """Anti-regresión bug run fe86d4e4 (NameError: EpisodeClosedDecision).

    `run_agent_turn` construye 4 dataclasses de decisión DENTRO del cuerpo de
    la función (resolución diferida): TransferDecision,
    ScheduleRemarketingDecision, EscalationDecision y EpisodeClosedDecision.
    Si alguno falta en el `imports_passed_through()` del módulo, éste importa
    limpio pero el workflow truena en runtime con NameError cuando ese payload
    aparece (caso EpisodeClosedDecision: al cerrar el episodio tras confirmar
    el pedido → el cliente toca '✅ Confirmar' y el workflow queda en retry
    infinito). Este guard falla si se vuelve a soltar cualquiera de los 4."""
    import src.platform.workflow_helpers as wh

    for symbol in (
        "TransferDecision",
        "ScheduleRemarketingDecision",
        "EscalationDecision",
        "EpisodeClosedDecision",
    ):
        assert hasattr(wh, symbol), (
            f"{symbol} no está en el namespace de workflow_helpers — "
            "run_agent_turn lo usaría y tiraría NameError en runtime"
        )


def test_turn_result_with_episode_closed_serializable() -> None:
    """El campo `episode_closed_decision` viaja workflow→activity (R-JSON)."""
    from src.platform.contracts import EpisodeClosedDecision

    tr = TurnResult(
        final_content="listo",
        tools_used=["manage_conversation_tag"],
        episode_closed_decision=EpisodeClosedDecision(
            session_id="wa_C", episode_id="ep_001", closing_tag="COMPRA_EXITOSA"
        ),
    )
    payload = asdict(tr)
    assert payload["episode_closed_decision"]["episode_id"] == "ep_001"
    assert payload["episode_closed_decision"]["closing_tag"] == "COMPRA_EXITOSA"
